"""IncidentDesk, REST API and MCP server with the real IncidentService over fake memory, advisor and shop."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent.desk import IncidentDesk, UnknownIncident
from agent.memory import IncidentMemoryError
from agent.models import ShopFastAlert
from agent.service import IncidentService
from api import app as api
from mcp_server import server as mcp
from tests.test_service import BROKEN, HEALTHY, ActionAdvisor, FakeShop, PostmortemMemory

ALERT = ShopFastAlert(id=1, at="2026-09-28T11:27:07Z", method="POST", path="/checkout", status=503,
                      error="Orders database unavailable",
                      log="2026-09-28T11:27:07Z ERROR payment-api sqlalchemy.exc.OperationalError: FATAL", count=3)
NEW = dict(service="payment-api", severity="SEV1", title="Checkout failing", symptoms="503 on checkout",
           error_log="FATAL: remaining connection slots are reserved")
KEY = "test-key-123"


class DetectingShop(FakeShop):
    """Broken until an action runs, then healthy."""

    def __init__(self, broken=True):
        super().__init__(health=BROKEN if broken else HEALTHY)

    def run_action(self, name):
        super().run_action(name)
        self._health = HEALTHY

    def latest_incident(self):
        return ALERT


def _desk(broken=True, memory=None, max_open=200):
    service = IncidentService(memory or PostmortemMemory(), ActionAdvisor(), shop=DetectingShop(broken))
    return IncidentDesk(service, max_open=max_open)


# desk

def test_desk_detect_analyze_act_postmortem_flow():
    memory = PostmortemMemory()
    desk = _desk(memory=memory)
    item = desk.detect()
    assert item.incident.service == "payment-api" and item.suggestion.proposed_action == "rollback_payment_api"
    attempt = desk.act(item.incident.incident_id)
    assert attempt.verified and attempt.chosen_by == "agent"
    assert desk.get(item.incident.incident_id).resolved
    postmortem = desk.postmortem(item.incident.incident_id)
    assert postmortem.saved_to_memory
    context = next(c[2] for c in memory.calls if c[0] == "reflect_postmortem")
    assert "rollback_payment_api" in context and "shop healthy afterwards" in context


def test_desk_detect_returns_none_when_healthy():
    assert _desk(broken=False).detect() is None


def test_desk_unknown_incident_and_bounded_store():
    desk = _desk(max_open=1)
    with pytest.raises(UnknownIncident):
        desk.get("INC-9999")
    from agent.models import Incident
    first = desk.analyze(Incident(**NEW))
    desk.analyze(Incident(**NEW))
    with pytest.raises(UnknownIncident):
        desk.get(first.incident.incident_id)


def test_desk_reanalyze_passes_tried_actions():
    desk = _desk()
    item = desk.detect()
    desk.act(item.incident.incident_id, "restart_payment_api_pods")
    desk.reanalyze(item.incident.incident_id)
    advisor = desk._service._advisor
    assert advisor.calls[-1][4] == ["restart_payment_api_pods"]


# REST API

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", KEY)
    desk = _desk()
    api.app.dependency_overrides[api.get_desk] = lambda: desk
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def _h(key=KEY):
    return {"X-API-Key": key}


def test_api_health_needs_no_key(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_api_rejects_missing_or_wrong_key(client, headers):
    assert client.post("/incidents/detect", headers=headers).status_code == 401


def test_api_refuses_everything_without_configured_key(client, monkeypatch):
    monkeypatch.delenv("AGENT_API_KEY")
    assert client.post("/incidents/detect", headers=_h()).status_code == 503


def test_api_full_flow(client):
    item = client.post("/incidents/analyze", json=NEW, headers=_h()).json()
    incident_id = item["incident"]["incident_id"]
    assert item["suggestion"]["proposed_action"] == "rollback_payment_api" and item["resolved"] is False
    attempt = client.post(f"/incidents/{incident_id}/actions", json={}, headers=_h()).json()
    assert attempt["verified"] is True
    assert client.get(f"/incidents/{incident_id}", headers=_h()).json()["resolved"] is True
    outcome = {"resolved": True, "actual_root_cause": "Pool too small", "steps_that_worked": ["Roll back"]}
    assert client.post(f"/incidents/{incident_id}/outcome", json=outcome, headers=_h()).status_code == 204
    postmortem = client.post(f"/incidents/{incident_id}/postmortem", headers=_h()).json()
    assert postmortem["incident_id"] == "INC-2001" and postmortem["saved_to_memory"] is True
    assert client.get("/runbook", headers=_h()).json() == {"runbook": "runbook table"}
    assert client.get("/memory/search", params={"q": "pool", "limit": 2}, headers=_h()).json() == {"results": ["pool:2"]}


def test_api_detect_healthy_returns_null(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", KEY)
    api.app.dependency_overrides[api.get_desk] = lambda: _desk(broken=False)
    try:
        assert TestClient(api.app).post("/incidents/detect", headers=_h()).json() is None
    finally:
        api.app.dependency_overrides.clear()


def test_api_errors_map_to_status_codes(client):
    assert client.get("/incidents/INC-9999", headers=_h()).status_code == 404
    assert client.post("/incidents/analyze", json={**NEW, "service": "Bad Service"}, headers=_h()).status_code == 422
    incident_id = client.post("/incidents/analyze", json=NEW, headers=_h()).json()["incident"]["incident_id"]
    assert client.post(f"/incidents/{incident_id}/actions", json={"action": "drop_database"},
                       headers=_h()).status_code == 409
    assert client.post(f"/incidents/{incident_id}/outcome", json={"resolved": True, "actual_root_cause": "x"},
                       headers=_h()).status_code == 422


def test_api_memory_failure_is_502(monkeypatch):
    class Down(PostmortemMemory):
        def recall_similar(self, incident):
            raise IncidentMemoryError("Hindsight down")

    monkeypatch.setenv("AGENT_API_KEY", KEY)
    api.app.dependency_overrides[api.get_desk] = lambda: _desk(memory=Down())
    try:
        response = TestClient(api.app).post("/incidents/analyze", json=NEW, headers=_h())
        assert response.status_code == 502 and "Hindsight down" in response.json()["detail"]
    finally:
        api.app.dependency_overrides.clear()


# MCP server

@pytest.fixture
def mcp_desk():
    desk = _desk()
    mcp.set_desk(desk)
    yield desk
    mcp.set_desk(None)


def test_mcp_lists_tools_with_destructive_hints():
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    assert set(tools) == {"detect_incident", "analyze_incident", "run_action", "next_suggestion", "record_outcome",
                          "write_postmortem", "get_runbook", "search_memory"}
    assert tools["run_action"].annotations.destructive_hint is True
    assert tools["analyze_incident"].annotations.read_only_hint is True


def test_mcp_flow(mcp_desk):
    item = mcp.detect_incident()
    incident_id = item["incident"]["incident_id"]
    assert item["proposed_action"] == "rollback_payment_api" and item["memory_used"] is True
    assert mcp.run_action(incident_id)["verified"] is True
    assert mcp.write_postmortem(incident_id)["saved_to_memory"] is True
    assert mcp.record_outcome(incident_id, True, "Pool too small", ["Roll back"])["recorded"] is True
    assert mcp.get_runbook() == {"runbook": "runbook table"}
    assert mcp.search_memory("pool", 500) == {"results": ["pool:50"]}


def test_mcp_errors_are_returned_not_raised(mcp_desk):
    assert "Unknown incident" in mcp.run_action("INC-9999")["error"]
    assert "error" in mcp.analyze_incident("Bad Service", "SEV1", "Checkout failing", "503")
    incident_id = mcp.analyze_incident(**NEW)["incident"]["incident_id"]
    assert "not allow-listed" in mcp.run_action(incident_id, "drop_database")["error"]


def test_mcp_detect_when_healthy():
    mcp.set_desk(_desk(broken=False))
    try:
        assert mcp.detect_incident()["healthy"] is True
    finally:
        mcp.set_desk(None)
