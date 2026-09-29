"""SHOPFAST_ADMIN_TOKEN: when set (public deploy), fault switches and runbook actions need the X-Admin-Token header.
Unset (local), ShopFast behaves as before."""

import pytest
from fastapi.testclient import TestClient

from shopfast import app as shop

TOKEN = "judge-demo-token-123"
PROTECTED = [("GET", "/admin/faults"), ("POST", "/admin/faults/DB_POOL_EXHAUST"),
             ("DELETE", "/admin/faults/DB_POOL_EXHAUST"), ("GET", "/ops/actions"),
             ("POST", "/ops/actions/rollback_payment_api"), ("GET", "/ops/incidents/latest")]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    monkeypatch.setattr(shop, "alerts", shop.AlertLog())
    return TestClient(shop.app)


@pytest.mark.parametrize("method, path", PROTECTED)
def test_protected_endpoints_need_the_token_when_set(client, monkeypatch, method, path):
    monkeypatch.setenv("SHOPFAST_ADMIN_TOKEN", TOKEN)
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.request(method, path, headers={"X-Admin-Token": TOKEN}).status_code in (200, 404)


def test_customer_endpoints_stay_open_with_a_token(client, monkeypatch):
    monkeypatch.setenv("SHOPFAST_ADMIN_TOKEN", TOKEN)
    assert client.get("/products").status_code == 200
    assert client.post("/checkout", json={"items": [{"product_id": "sku-100", "quantity": 1}]}).status_code == 200


def test_no_token_configured_keeps_local_behaviour(client, monkeypatch):
    monkeypatch.delenv("SHOPFAST_ADMIN_TOKEN", raising=False)
    assert client.post("/admin/faults/DB_POOL_EXHAUST").status_code == 200
    assert client.get("/ops/actions").status_code == 200
