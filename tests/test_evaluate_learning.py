"""Before-vs-after learning evaluation harness, tested offline.

Real IncidentService and in-process ShopFast; memory is a small in-process store that recalls what was retained
(same stored text as Hindsight gets); the advisor is scripted: without evidence it tries a decoy first, with
evidence it proposes the action that worked. So the harness's measurements are checked, not the LLM.
"""

import json

import pytest
from fastapi.testclient import TestClient

from agent.actions import ShopFastClient
from agent.memory import format_outcome
from agent.models import SimilarIncident, Suggestion
from agent.service import IncidentService
from scripts import evaluate_learning as ev
from shopfast import app as shop
from shopfast.faults import FAULT_LOGS, Fault
from shopfast.remediations import ACTIONS

FIXER = {fault: next(n for n, a in ACTIONS.items() if fault in a.fixes) for fault in Fault}
DECOY = "restart_payment_api_pods"
SIGNATURE = {fault: FAULT_LOGS[fault].split(": ")[0] for fault in Fault}  # e.g. "ERROR payment-api httpx.ReadTimeout"


class StoreMemory:
    def __init__(self):
        self.stored: dict[str, str] = {}

    def recall_similar(self, incident):
        return [SimilarIncident(incident_id=i, summary="", source_text=t) for i, t in self.stored.items()
                if i != incident.incident_id]

    def recall_learned_patterns(self, incident):
        return []

    def retain_outcome(self, incident, outcome):
        self.stored[incident.incident_id] = format_outcome(incident, outcome)

    def team_rules(self):
        return []


class ScriptedAdvisor:
    """Relevant = stored outcomes for the same log. No memory: decoy first, then the fixer."""

    def suggest(self, incident, similar, patterns, actions=(), tried_actions=(), rules=()):
        fault = next(f for f in Fault if SIGNATURE[f] in incident.error_log)
        relevant = [s for s in similar if SIGNATURE[fault] in s.source_text]
        proven = [s for s in relevant if f"- {FIXER[fault]}:" in s.source_text.split("did not work")[0]]
        if proven:
            action = FIXER[fault]
        else:
            action = next(a for a in (DECOY, FIXER[fault]) if a not in tried_actions)
        return Suggestion(similar_incidents=relevant, learned_patterns=[], probable_root_cause="cause",
                          confidence="high" if relevant else "low", memory_used=bool(relevant),
                          proposed_action=action, action_reason="scripted")


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    monkeypatch.setattr(shop, "alerts", shop.AlertLog())
    http = TestClient(shop.app)
    memory = StoreMemory()
    return IncidentService(memory, ScriptedAdvisor(), shop=ShopFastClient(http)), http, memory


def test_before_round_without_memory_tries_decoy_then_fixes(setup):
    service, http, _ = setup
    result = ev.run_round(service, http, Fault.DB_POOL_EXHAUST)
    assert result.memory_used is False
    assert result.actions_tried == [DECOY, "rollback_payment_api"]
    assert (result.attempts, result.resolved, result.first_try_fix) == (2, True, False)
    assert http.get("/admin/faults").json()["active"] == []  # harness cleans up


def test_after_round_uses_what_was_learned(setup):
    service, http, _ = setup
    before = ev.run_round(service, http, Fault.DB_POOL_EXHAUST)
    after = ev.run_round(service, http, Fault.DB_POOL_EXHAUST)
    comparison = ev.compare(before, after)
    assert comparison.recalled_learned_incident is True
    assert comparison.proposed_proven_fix_first is True
    assert comparison.failed_fixes_avoided == [DECOY]
    assert (after.attempts, after.first_try_fix) == (1, True)


def test_evaluate_runs_all_befores_then_all_afters_and_summarizes(setup, tmp_path):
    service, http, _ = setup
    faults = [Fault.DB_POOL_EXHAUST, Fault.PAYMENT_GATEWAY_TIMEOUT]
    report = ev.evaluate(service, http, faults, wait=lambda: None)
    s = report["summary"]
    assert s["incidents"] == 2
    assert (s["relevant_recall_before"], s["relevant_recall_after"]) == (0, 2)
    assert (s["first_try_fix_before"], s["first_try_fix_after"]) == (0, 2)
    assert (s["attempts_before"], s["attempts_after"]) == (4, 2)
    assert (s["failed_fixes_before"], s["failed_fixes_avoided_after"]) == (2, 2)
    assert s["approvals"] == "given by the evaluation harness"
    out = tmp_path / "results.json"
    ev.write_report(report, out)
    assert json.loads(out.read_text())["summary"] == s


class SilentAdvisor(ScriptedAdvisor):
    """Proposes nothing until memory shows a verified fix, like the LLM did for REDIS_TIMEOUT."""

    def suggest(self, incident, similar, patterns, actions=(), tried_actions=(), rules=()):
        suggestion = super().suggest(incident, similar, patterns, actions, tried_actions)
        if not suggestion.memory_used:
            return suggestion.model_copy(update={"proposed_action": None, "action_reason": ""})
        return suggestion


def test_engineer_fallback_runs_when_agent_proposes_nothing_and_agent_learns_from_it(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    monkeypatch.setattr(shop, "alerts", shop.AlertLog())
    http = TestClient(shop.app)
    service = IncidentService(StoreMemory(), SilentAdvisor(), shop=ShopFastClient(http))
    before = ev.run_round(service, http, Fault.REDIS_TIMEOUT)
    runbook = list(ACTIONS)
    fixer_at = runbook.index(FIXER[Fault.REDIS_TIMEOUT])
    assert before.resolved and not before.agent_fixed
    assert before.engineer_actions == runbook[:fixer_at + 1] == before.actions_tried
    after = ev.run_round(service, http, Fault.REDIS_TIMEOUT)
    assert after.agent_fixed and after.actions_tried == [FIXER[Fault.REDIS_TIMEOUT]]
    assert after.engineer_actions == []


def test_refuses_to_run_against_the_main_bank():
    with pytest.raises(SystemExit):
        ev.main(["--bank-id", "shopfast-incidents"])

