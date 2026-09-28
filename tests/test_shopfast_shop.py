"""ShopFast shop endpoint tests: normal behavior and each fault's failure."""

import logging
import re

import pytest
from fastapi.testclient import TestClient

from shopfast import app as shop
from shopfast.faults import FAULT_LOGS, Fault

ITEMS = {"items": [{"product_id": "sku-100", "quantity": 2}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    return TestClient(shop.app)


def _enable(client, fault: Fault):
    assert client.post(f"/admin/faults/{fault.value}").status_code == 200


# normal behavior

def test_products_listed(client):
    response = client.get("/products")
    assert response.status_code == 200
    products = response.json()
    assert len(products) >= 3
    assert {"id", "name", "price"} <= set(products[0])


def test_add_to_cart(client):
    response = client.post("/cart/items", json={"product_id": "sku-100", "quantity": 2})
    assert response.status_code == 200
    assert response.json()["quantity"] == 2


@pytest.mark.parametrize("body, status", [
    ({"product_id": "sku-missing", "quantity": 1}, 404),
    ({"product_id": "sku-100", "quantity": 0}, 422),
    ({"product_id": "sku-100", "quantity": 11}, 422),
    ({"quantity": 1}, 422),
])
def test_add_to_cart_rejects_bad_input(client, body, status):
    assert client.post("/cart/items", json=body).status_code == status


def test_login(client):
    response = client.post("/login", json={"username": "alice", "password": "secret"})
    assert response.status_code == 200
    assert response.json()["token"]


@pytest.mark.parametrize("body", [{"username": "", "password": "x"}, {"username": "alice"}])
def test_login_rejects_bad_input(client, body):
    assert client.post("/login", json=body).status_code == 422


def test_checkout_returns_order_with_total(client):
    price = next(p["price"] for p in client.get("/products").json() if p["id"] == "sku-100")
    response = client.post("/checkout", json=ITEMS)
    assert response.status_code == 200
    body = response.json()
    assert re.fullmatch(r"ORD-\d+", body["order_id"])
    assert body["total"] == pytest.approx(price * 2)


@pytest.mark.parametrize("body, status", [
    ({"items": []}, 422),
    ({"items": [{"product_id": "sku-missing", "quantity": 1}]}, 404),
])
def test_checkout_rejects_bad_input(client, body, status):
    assert client.post("/checkout", json=body).status_code == status


# faults

@pytest.mark.parametrize("fault, method, path, body, status", [
    (Fault.REDIS_TIMEOUT, "get", "/products", None, 503),
    (Fault.REDIS_TIMEOUT, "post", "/cart/items", {"product_id": "sku-100", "quantity": 1}, 503),
    (Fault.AUTH_TOKEN_EXPIRED, "post", "/login", {"username": "alice", "password": "secret"}, 401),
    (Fault.DB_POOL_EXHAUST, "post", "/checkout", ITEMS, 503),
    (Fault.PAYMENT_GATEWAY_TIMEOUT, "post", "/checkout", ITEMS, 504),
    (Fault.INVENTORY_DEADLOCK, "post", "/checkout", ITEMS, 500),
    (Fault.CERT_EXPIRED, "post", "/login", {"username": "alice", "password": "secret"}, 502),
    (Fault.SEARCH_DISK_FULL, "get", "/products", None, 503),
    (Fault.PROMO_CONFIG_BROKEN, "post", "/checkout", ITEMS, 500),
], ids=lambda v: v.value if isinstance(v, Fault) else None)
def test_fault_fails_endpoint_with_its_log_line(client, caplog, fault, method, path, body, status):
    _enable(client, fault)
    with caplog.at_level(logging.ERROR, logger="shopfast"):
        response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == status
    log = response.json()["log"]
    assert log.endswith(FAULT_LOGS[fault])
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ", log)  # timestamped like a real log line
    assert FAULT_LOGS[fault] in caplog.text


@pytest.mark.parametrize("fault, unaffected", [
    (Fault.REDIS_TIMEOUT, ("post", "/checkout", ITEMS)),
    (Fault.AUTH_TOKEN_EXPIRED, ("get", "/products", None)),
    (Fault.DB_POOL_EXHAUST, ("post", "/login", {"username": "alice", "password": "secret"})),
    (Fault.PAYMENT_GATEWAY_TIMEOUT, ("post", "/cart/items", {"product_id": "sku-100", "quantity": 1})),
    (Fault.INVENTORY_DEADLOCK, ("get", "/products", None)),
    (Fault.CERT_EXPIRED, ("post", "/checkout", ITEMS)),
    (Fault.SEARCH_DISK_FULL, ("post", "/cart/items", {"product_id": "sku-100", "quantity": 1})),
    (Fault.PROMO_CONFIG_BROKEN, ("post", "/login", {"username": "alice", "password": "secret"})),
], ids=lambda v: v.value if isinstance(v, Fault) else None)
def test_fault_does_not_break_other_endpoints(client, fault, unaffected):
    _enable(client, fault)
    method, path, body = unaffected
    assert getattr(client, method)(path, **({"json": body} if body else {})).status_code == 200


def test_disabling_fault_restores_checkout(client):
    _enable(client, Fault.DB_POOL_EXHAUST)
    assert client.post("/checkout", json=ITEMS).status_code == 503
    client.delete(f"/admin/faults/{Fault.DB_POOL_EXHAUST.value}")
    assert client.post("/checkout", json=ITEMS).status_code == 200


def test_db_fault_wins_when_both_checkout_faults_are_on(client):
    _enable(client, Fault.PAYMENT_GATEWAY_TIMEOUT)
    _enable(client, Fault.DB_POOL_EXHAUST)
    assert client.post("/checkout", json=ITEMS).status_code == 503


def test_invalid_input_is_rejected_before_fault(client):
    _enable(client, Fault.DB_POOL_EXHAUST)
    assert client.post("/checkout", json={"items": []}).status_code == 422
