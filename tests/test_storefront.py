"""Storefront: a customer-facing page that forwards three customer calls to the real ShopFast API, unchanged."""

import httpx
import pytest
from fastapi.testclient import TestClient

from shopfast import app as shop
from shopfast.faults import FAULT_LOGS, Fault
from storefront import app as store

ITEMS = {"items": [{"product_id": "sku-100", "quantity": 2}]}


@pytest.fixture
def shopfast(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    monkeypatch.setattr(shop, "alerts", shop.AlertLog())
    return TestClient(shop.app)


@pytest.fixture
def storefront(shopfast, monkeypatch):
    monkeypatch.setattr(store, "shopfast_client", lambda: shopfast)  # in-process ShopFast instead of the network
    return TestClient(store.app)


def test_page_is_served_and_uses_only_storefront_api(storefront):
    response = storefront.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    assert "ShopFast" in page
    for path in ("/api/products", "/api/cart/items", "/api/checkout"):
        assert path in page
    assert "/admin" not in page and "/ops" not in page and "innerHTML" not in page


def test_products_come_from_shopfast(storefront, shopfast):
    assert storefront.get("/api/products").json() == shopfast.get("/products").json()


def test_add_to_cart_passes_through(storefront):
    ok = storefront.post("/api/cart/items", json={"product_id": "sku-100", "quantity": 1})
    assert ok.status_code == 200 and ok.json() == {"product_id": "sku-100", "quantity": 1}
    assert storefront.post("/api/cart/items", json={"product_id": "sku-missing", "quantity": 1}).status_code == 404


def test_healthy_checkout_returns_real_order(storefront):
    response = storefront.post("/api/checkout", json=ITEMS)
    assert response.status_code == 200
    assert response.json()["order_id"].startswith("ORD-")
    assert response.json()["total"] == pytest.approx(179.98)


def test_fault_status_and_error_are_shopfasts_own(storefront, shopfast):
    shopfast.post("/admin/faults/DB_POOL_EXHAUST")
    response = storefront.post("/api/checkout", json=ITEMS)
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "Orders database unavailable"
    assert body["log"].endswith(FAULT_LOGS[Fault.DB_POOL_EXHAUST])


def test_storefront_failure_is_what_the_agent_detects(storefront, shopfast):
    shopfast.post("/admin/faults/DB_POOL_EXHAUST")
    storefront.post("/api/checkout", json=ITEMS)
    alert = shopfast.get("/ops/incidents/latest").json()
    assert (alert["method"], alert["path"], alert["status"]) == ("POST", "/checkout", 503)


def test_checkout_recovers_after_remediation(storefront, shopfast):
    shopfast.post("/admin/faults/DB_POOL_EXHAUST")
    assert storefront.post("/api/checkout", json=ITEMS).status_code == 503
    shopfast.post("/ops/actions/rollback_payment_api")
    assert storefront.post("/api/checkout", json=ITEMS).status_code == 200


def test_validation_errors_pass_through(storefront):
    assert storefront.post("/api/checkout", json={"items": []}).status_code == 422


@pytest.mark.parametrize("method, path", [("GET", "/api/admin/faults"), ("POST", "/api/admin/faults/DB_POOL_EXHAUST"),
                                          ("GET", "/api/ops/actions"), ("POST", "/api/ops/actions/rollback_payment_api"),
                                          ("GET", "/api/ops/incidents/latest"), ("POST", "/api/login")])
def test_admin_ops_and_other_endpoints_are_not_forwarded(storefront, method, path):
    assert storefront.request(method, path).status_code in (404, 405)


def test_unreachable_shopfast_is_reported_as_such(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    down = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://shopfast.test")
    monkeypatch.setattr(store, "shopfast_client", lambda: down)
    response = TestClient(store.app).post("/api/checkout", json=ITEMS)
    assert response.status_code == 502
    assert response.json()["error"] == "ShopFast unreachable"


def test_shopfast_url_from_environment(monkeypatch):
    monkeypatch.setenv("SHOPFAST_HOST", "127.0.0.1")
    monkeypatch.setenv("SHOPFAST_PORT", "8001")
    assert store.shopfast_url() == "http://127.0.0.1:8001"


def test_shopfast_url_overrides_host_and_port(monkeypatch):
    monkeypatch.setenv("SHOPFAST_URL", "https://shopfast-demo.onrender.com/")
    assert store.shopfast_url() == "https://shopfast-demo.onrender.com"


# product images

import re  # noqa: E402

from shopfast.app import PRODUCTS  # noqa: E402


def _image_paths(page: str) -> dict[str, str]:
    return dict(re.findall(r'"(sku-\d+)":\s*"(/static/images/[a-z-]+\.svg)"', page))


def test_every_shopfast_product_has_an_image(storefront):
    images = _image_paths(storefront.get("/").text)
    assert set(images) == set(PRODUCTS)


def test_images_are_served_as_svg_without_scripts(storefront):
    for path in _image_paths(storefront.get("/").text).values():
        response = storefront.get(path)
        assert response.status_code == 200, path
        assert "image/svg+xml" in response.headers["content-type"]
        assert "<script" not in response.text.lower() and "href=" not in response.text.lower()


def test_unknown_image_is_not_found(storefront):
    assert storefront.get("/static/images/missing.svg").status_code == 404
    assert storefront.get("/static/../app.py").status_code == 404
