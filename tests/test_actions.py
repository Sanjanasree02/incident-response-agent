"""ShopFastClient tests, run in-process against the real ShopFast app (FastAPI TestClient is an httpx.Client)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import config
from agent.actions import HEALTH_CHECKS, ShopFastClient, ShopFastError
from agent.config import load_settings
from agent.models import RemediationAction
from shopfast import app as shop
from shopfast.remediations import ACTIONS


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    return TestClient(shop.app)


def test_list_actions_returns_allow_list(http):
    actions = ShopFastClient(http).list_actions()
    assert all(isinstance(a, RemediationAction) for a in actions)
    assert {a.name for a in actions} == set(ACTIONS)


def test_health_check_reports_every_endpoint(http):
    health = ShopFastClient(http).health_check()
    assert set(health) == {f"{method} {path}" for method, path, _ in HEALTH_CHECKS}
    assert all(status == 200 for status in health.values())


def test_health_check_shows_the_failing_endpoint(http):
    http.post("/admin/faults/DB_POOL_EXHAUST")
    health = ShopFastClient(http).health_check()
    assert health["POST /checkout"] == 503
    assert health["GET /products"] == 200


def test_run_action_executes_allow_listed_action(http):
    http.post("/admin/faults/PAYMENT_GATEWAY_TIMEOUT")
    client = ShopFastClient(http)
    client.run_action("raise_payment_gateway_timeout")
    assert client.health_check()["POST /checkout"] == 200


@pytest.mark.parametrize("name", ["drop_database", "../admin/faults/DB_POOL_EXHAUST", "RAISE"])
def test_run_action_refuses_names_outside_the_allow_list(http, name):
    with pytest.raises(ShopFastError):
        ShopFastClient(http).run_action(name)


def test_client_never_touches_fault_admin(http):
    seen = []
    http.event_hooks["request"].append(lambda request: seen.append(request.url.path))
    client = ShopFastClient(http)
    client.list_actions()
    client.health_check()
    client.run_action("restart_payment_api_pods")
    assert seen and not any(path.startswith("/admin") for path in seen)


def test_unreachable_shopfast_raises_shopfast_error():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client = ShopFastClient(httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://shop.test"))
    with pytest.raises(ShopFastError):
        client.list_actions()


def test_settings_build_shopfast_url(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for name in ["HINDSIGHT_BASE_URL", "HINDSIGHT_API_KEY", "GROQ_API_KEY"]:
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("SHOPFAST_HOST", "127.0.0.1")
    monkeypatch.setenv("SHOPFAST_PORT", "8001")
    assert load_settings().shopfast_url == "http://127.0.0.1:8001"


def test_shopfast_url_overrides_host_and_port_for_a_deployed_shop(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for name in ["HINDSIGHT_BASE_URL", "HINDSIGHT_API_KEY", "GROQ_API_KEY"]:
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("SHOPFAST_URL", "https://shopfast-demo.onrender.com/")
    assert load_settings().shopfast_url == "https://shopfast-demo.onrender.com"


def test_client_sends_the_admin_token_when_configured(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for name in ["HINDSIGHT_BASE_URL", "HINDSIGHT_API_KEY", "GROQ_API_KEY"]:
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("SHOPFAST_ADMIN_TOKEN", "tok-123")
    client = ShopFastClient.from_settings(load_settings())
    assert client._http.headers["X-Admin-Token"] == "tok-123"
    monkeypatch.delenv("SHOPFAST_ADMIN_TOKEN")
    assert "X-Admin-Token" not in ShopFastClient.from_settings(load_settings())._http.headers
