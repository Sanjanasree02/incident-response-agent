"""Streamlit UI tests with AppTest and a fake IncidentService in session state. No network calls."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from agent import config
from agent.memory import IncidentMemoryError
from agent.models import (LearnedPattern, Postmortem, RemediationAction, RemediationAttempt, SimilarIncident,
                          Suggestion, TeamRule)
from shopfast.faults import FAULT_LOGS, Fault

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")

SIMILAR = [SimilarIncident(incident_id="INC-1042", summary="Pool exhausted after deploy",
                           source_text="Incident INC-1042 full stored text")]
PATTERNS = [LearnedPattern(text="Restarting pods never fixes pool exhaustion")]


def _suggestion(**overrides) -> Suggestion:
    data = dict(similar_incidents=SIMILAR, learned_patterns=PATTERNS,
                probable_root_cause="Connection pool exhausted (INC-1042)",
                fix_steps=["Roll back payment-api (INC-1042)", "Lower pool_size"],
                avoid_steps=["Restarting pods (INC-1042)"], confidence="high", memory_used=True)
    data.update(overrides)
    return Suggestion(**data)


class FakeService:
    def __init__(self, suggestion=None, error=None, next_suggestion=None, verified=True, detected=None,
                 detect_error=None, postmortem_error=None, runbook="| failure | fix |"):
        self.analyzed, self.recorded, self.tried, self.remediated = [], [], [], []
        self._postmortem_error, self._runbook = postmortem_error, runbook
        self.rules = [TeamRule(name="prefer-rollback-after-change", content="Prefer rolling back a recent change.",
                               priority=20)]
        self._detected, self._detect_error = detected, detect_error
        self._suggestion = suggestion or _suggestion()
        self._next = next_suggestion
        self._error = error
        self._verified = verified

    def analyze_incident(self, incident, tried_actions=()):
        self.analyzed.append(incident)
        self.tried.append(list(tried_actions))
        if self._error:
            raise self._error
        return self._next if tried_actions and self._next else self._suggestion

    def detect_incident(self):
        if self._detect_error:
            raise self._detect_error
        return self._detected

    def remediate(self, incident, suggestion, approved, previous_attempts=(), action=None):
        name = action or suggestion.proposed_action
        self.remediated.append((incident, name, approved, list(previous_attempts)))
        verified = approved and self._verified
        return RemediationAttempt(
            action=name, description=f"desc of {name}", chosen_by="engineer" if action else "agent",
            reason=suggestion.action_reason, approved=approved, executed=approved, verified=verified,
            health={"POST /checkout": 200 if verified else 503} if approved else {})

    def record_outcome(self, incident, outcome):
        self.recorded.append((incident, outcome))

    def list_actions(self):
        return [RemediationAction(name=n, description=f"desc of {n}")
                for n in ("rollback_payment_api", "restart_payment_api_pods", "disable_cart_analytics")]

    def write_postmortem(self, incident, suggestion, attempts):
        self.postmortem_attempts = list(attempts)
        if self._postmortem_error:
            raise self._postmortem_error
        return Postmortem(incident_id=incident.incident_id, summary="Checkout down 12 min", impact="All orders",
                          root_cause="Pool halved", action_items=["Alert on pool usage"],
                          related_incidents=["INC-1042"], saved_to_memory=True)

    def runbook(self):
        return self._runbook

    def team_rules(self):
        return list(self.rules)

    def add_team_rule(self, rule):
        self.rules.append(rule)


def _app(service: FakeService) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["service"] = service
    return at.run()


def _submit(at: AppTest, title="Checkout failing", symptoms="503 on checkout", log="FATAL: too many clients"):
    at.text_input(key="service_name").input("payment-api")
    at.selectbox(key="severity").select("SEV1")
    at.text_input(key="title").input(title)
    at.text_area(key="symptoms").input(symptoms)
    at.text_area(key="error_log").input(log)
    return at.button(key="analyze").click().run()


def _all_text(at: AppTest) -> str:
    parts = []
    for kind in ("text", "markdown", "caption", "code", "info", "warning", "error", "success", "subheader"):
        parts += [str(e.value) for e in getattr(at, kind)]
    return "\n".join(parts)


# submit tab

def test_app_starts_without_errors():
    at = _app(FakeService())
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Submit incident", "Record outcome", "What the agent has learned",
                                          "Integrate"]


def test_submit_sends_incident_and_shows_suggestion():
    service = FakeService()
    at = _submit(_app(service))
    assert not at.exception
    [incident] = service.analyzed
    assert (incident.service, incident.severity.value, incident.title) == ("payment-api", "SEV1", "Checkout failing")
    assert incident.error_log == "FATAL: too many clients"
    text = _all_text(at)
    for part in [incident.incident_id, "Connection pool exhausted (INC-1042)", "Roll back payment-api (INC-1042)",
                 "Lower pool_size", "Restarting pods (INC-1042)", "high", "INC-1042", "Pool exhausted after deploy",
                 "Incident INC-1042 full stored text"]:
        assert part in text


def test_no_relevant_memory_is_stated():
    at = _submit(_app(FakeService(_suggestion(similar_incidents=[], memory_used=False, confidence="low"))))
    assert any("no similar past incident" in e.value.lower() for e in at.info)


def test_llm_failure_shows_warning_and_still_shows_memory():
    suggestion = _suggestion(llm_error="LLM failed after 3 attempts", fix_steps=[], avoid_steps=[],
                             probable_root_cause="AI suggestion unavailable.", confidence="low")
    at = _submit(_app(FakeService(suggestion)))
    assert any("LLM failed after 3 attempts" in w.value for w in at.warning)
    assert "INC-1042" in _all_text(at)


def test_invalid_input_shows_error_and_does_not_analyze():
    service = FakeService()
    at = _submit(_app(service), title="ab")
    assert at.error and "title" in at.error[0].value.lower()
    assert service.analyzed == []


def test_memory_error_is_shown():
    at = _submit(_app(FakeService(error=IncidentMemoryError("Hindsight recall failed for bank 'x': down"))))
    assert not at.exception
    assert any("Hindsight recall failed" in e.value for e in at.error)


def test_llm_output_is_not_rendered_as_markdown():
    evil = "See ![x](https://evil.test/leak.png) and [click](https://evil.test)"
    at = _submit(_app(FakeService(_suggestion(probable_root_cause=evil, fix_steps=[evil]))))
    assert not any("evil.test" in m.value for m in at.markdown)
    assert any("evil.test" in t.value for t in at.text)


def test_example_log_fills_error_log():
    at = _app(FakeService())
    at.selectbox(key="example_fault").select("PAYMENT_GATEWAY_TIMEOUT")
    at = at.button(key="load_example").click().run()
    assert at.text_area(key="error_log").value == FAULT_LOGS[Fault.PAYMENT_GATEWAY_TIMEOUT]


# record outcome tab

def test_outcome_tab_asks_to_analyze_first():
    at = _app(FakeService())
    assert any("analyze an incident first" in i.value.lower() for i in at.info)


def test_record_outcome_sends_outcome_for_selected_incident():
    service = FakeService()
    at = _submit(_app(service))
    incident = service.analyzed[0]
    at.checkbox(key="resolved").check()
    at.text_area(key="actual_root_cause").input("Gateway timeout too low")
    at.text_area(key="steps_worked").input("Raise timeout to 30s\n\n  Add retries  \n")
    at.text_area(key="failed_attempts").input("Restarted pods")
    at.text_area(key="notes").input("Paygate status page degraded")
    at = at.button(key="record_outcome").click().run()
    assert not at.exception
    [(recorded_incident, outcome)] = service.recorded
    assert recorded_incident.incident_id == incident.incident_id
    assert outcome.incident_id == incident.incident_id
    assert outcome.resolved is True
    assert outcome.steps_that_worked == ["Raise timeout to 30s", "Add retries"]
    assert outcome.failed_attempts == ["Restarted pods"]
    assert any(incident.incident_id in s.value for s in at.success)


def test_invalid_outcome_shows_error():
    service = FakeService()
    at = _submit(_app(service))
    at.text_area(key="actual_root_cause").input("x")
    at = at.button(key="record_outcome").click().run()
    assert at.error
    assert service.recorded == []


# learned tab

def test_learned_tab_shows_patterns_from_last_analysis():
    at = _submit(_app(FakeService()))
    assert "Restarting pods never fixes pool exhaustion" in _all_text(at)


# configuration

def test_missing_configuration_is_shown_not_crashed(monkeypatch):
    st.cache_resource.clear()
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("HINDSIGHT_BASE_URL", "https://example.test")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "key")
    at = AppTest.from_file(APP, default_timeout=10).run()
    assert not at.exception
    assert any("GROQ_API_KEY" in e.value for e in at.error)
    st.cache_resource.clear()


def test_source_never_allows_unsafe_html():
    assert "unsafe_allow_html" not in Path(APP).read_text(encoding="utf-8").replace(
        "Render all user text with st.text / st.code / st.markdown without unsafe_allow_html.", "")


# act -> verify -> learn

def _with_action(action="rollback_payment_api", **overrides):
    return _suggestion(proposed_action=action, action_reason=f"{action} fixed INC-1042", **overrides)


def test_proposed_action_is_shown_with_reason_and_approval_buttons():
    at = _submit(_app(FakeService(_with_action())))
    text = _all_text(at)
    assert "rollback_payment_api" in text and "rollback_payment_api fixed INC-1042" in text
    assert at.button(key="approve_action") and at.button(key="reject_action")


def test_no_action_means_no_approval_buttons():
    at = _submit(_app(FakeService()))
    assert not [b for b in at.button if b.key in ("approve_action", "reject_action")]


def test_approve_runs_action_and_shows_verified_auto_recorded_result():
    service = FakeService(_with_action())
    at = _submit(_app(service))
    at = at.button(key="approve_action").click().run()
    assert not at.exception
    [(incident, action, approved, previous)] = service.remediated
    assert (action, approved, previous) == ("rollback_payment_api", True, [])
    assert any("recorded in memory automatically" in s.value.lower() for s in at.success)
    text = _all_text(at)
    assert "approved" in text.lower() and "POST /checkout 200" in text
    assert not [b for b in at.button if b.key == "approve_action"]  # resolved: nothing left to approve


def test_reject_records_decision_without_running_and_offers_next_action():
    service = FakeService(_with_action(), next_suggestion=_with_action("disable_cart_analytics"))
    at = _submit(_app(service))
    at = at.button(key="reject_action").click().run()
    assert service.remediated[0][2] is False
    assert "rejected" in _all_text(at).lower()
    at = at.button(key="next_action").click().run()
    assert service.tried[-1] == ["rollback_payment_api"]
    assert "disable_cart_analytics" in _all_text(at)


def test_failed_verification_shows_error_and_next_action_excludes_tried():
    service = FakeService(_with_action("restart_payment_api_pods"), verified=False,
                          next_suggestion=_with_action("rollback_payment_api"))
    at = _submit(_app(service))
    at = at.button(key="approve_action").click().run()
    assert any("still failing" in e.value.lower() for e in at.error)
    at = at.button(key="next_action").click().run()
    assert service.tried[-1] == ["restart_payment_api_pods"]
    at = at.button(key="approve_action").click().run()
    assert len(service.remediated[-1][3]) == 1  # earlier failed attempt passed along for the recorded outcome


# automatic intake

from agent.actions import ShopFastError  # noqa: E402
from agent.models import Incident  # noqa: E402

DETECTED = Incident(service="payment-api", severity="SEV1", title="POST /checkout returning 503: Orders database unavailable",
                    symptoms="Orders database unavailable. ShopFast recorded 1 failed request(s).",
                    error_log="2026-09-28T11:27:07Z ERROR payment-api FATAL: remaining connection slots are reserved")


def test_detect_opens_and_analyzes_the_incident_without_typing():
    service = FakeService(_with_action(), detected=DETECTED)
    at = _app(service).button(key="detect_incident").click().run()
    assert not at.exception
    assert service.analyzed == [DETECTED]
    text = _all_text(at)
    assert DETECTED.incident_id in text and "POST /checkout returning 503" in text
    assert "Connection pool exhausted (INC-1042)" in text


def test_detect_reports_healthy_shop():
    service = FakeService(detected=None)
    at = _app(service).button(key="detect_incident").click().run()
    assert any("no failing shopfast endpoint" in i.value.lower() for i in at.info)
    assert service.analyzed == []


def test_detect_reports_unreachable_shopfast():
    at = _app(FakeService(detect_error=ShopFastError("connection refused"))).button(key="detect_incident").click().run()
    assert not at.exception
    assert any("connection refused" in e.value for e in at.error)


def test_detected_incident_goes_through_the_approval_gate():
    service = FakeService(_with_action(), detected=DETECTED)
    at = _app(service).button(key="detect_incident").click().run()
    assert service.remediated == []  # nothing runs on detection alone
    at.button(key="approve_action").click().run()
    assert service.remediated[0][0] == DETECTED and service.remediated[0][2] is True


def test_manual_form_is_still_available():
    at = _app(FakeService())
    assert at.text_input(key="service_name") and at.button(key="analyze")


# learning evidence

import json  # noqa: E402

from agent.models import ActionEvidence  # noqa: E402

EVIDENCE = [
    ActionEvidence(action="rollback_payment_api", worked_in=["INC-26092810000001", "INC-26092812000003"]),
    ActionEvidence(action="restart_payment_api_pods", failed_in=["INC-26092810000001"]),
]


def test_evidence_shows_counts_and_incident_ids_from_memory():
    at = _submit(_app(FakeService(_with_action(action_evidence=EVIDENCE))))
    text = _all_text(at)
    assert "rollback_payment_api: worked in 2 of 2 recorded attempts" in text
    assert "restart_payment_api_pods: failed in 1 of 1 recorded attempts" in text
    assert "INC-26092812000003" in text
    assert "Last learned from INC-26092812000003" in text


def test_no_evidence_is_stated_not_invented():
    at = _submit(_app(FakeService(_with_action())))
    assert "no verified action outcomes in memory" in _all_text(at).lower()


def test_learned_tab_keeps_patterns_and_adds_evidence():
    at = _submit(_app(FakeService(_with_action(action_evidence=EVIDENCE))))
    text = _all_text(at)
    assert "Restarting pods never fixes pool exhaustion" in text  # Hindsight patterns stay
    assert text.count("rollback_payment_api: worked in 2 of 2") == 2  # analysis view and learned tab


def _eval_file(tmp_path):
    path = tmp_path / "evaluation_results.json"
    path.write_text(json.dumps({"generated_at": "2026-09-28T12:00:00Z", "summary": {
        "incidents": 4, "relevant_recall_before": 0, "relevant_recall_after": 4,
        "learned_incident_recalled_after": 4, "proven_fix_proposed_first_after": 3,
        "resolved_before": 4, "resolved_after": 4, "first_try_fix_before": 1, "first_try_fix_after": 3,
        "attempts_before": 8, "attempts_after": 5, "failed_fixes_before": 4, "failed_fixes_avoided_after": 4,
        "llm_errors": 0, "approvals": "given by the evaluation harness"}}))
    return path


def test_learned_tab_shows_before_after_evaluation(tmp_path):
    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["service"] = FakeService()
    at.session_state["eval_results_path"] = str(_eval_file(tmp_path))
    at.run()
    text = _all_text(at)
    assert "Relevant past incident recalled: 0/4 before, 4/4 after" in text
    assert "Fixed on the first action: 1/4 before, 3/4 after" in text
    assert "Actions needed: 8 before, 5 after" in text
    assert "Failed fixes avoided after learning: 4 of 4" in text


def test_learned_tab_explains_how_to_run_evaluation_when_missing(tmp_path):
    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["service"] = FakeService()
    at.session_state["eval_results_path"] = str(tmp_path / "missing.json")
    at.run()
    assert "scripts.evaluate_learning" in _all_text(at)


# engineer choice, postmortem, learning curve, runbook, integrate

def test_engineer_can_run_a_different_action_and_it_is_logged():
    service = FakeService(_with_action("restart_payment_api_pods"))
    at = _submit(_app(service))
    at.selectbox(key="engineer_action").select("disable_cart_analytics")
    at = at.button(key="run_engineer_action").click().run()
    assert not at.exception
    [(_, action, approved, _)] = service.remediated
    assert (action, approved) == ("disable_cart_analytics", True)
    assert "disable_cart_analytics (chosen by engineer)" in _all_text(at)


def test_engineer_choice_is_open_when_agent_proposes_nothing():
    at = _submit(_app(FakeService()))
    assert any(e.label.startswith("Run a different action") for e in at.expander)
    assert at.button(key="run_engineer_action")


def test_postmortem_after_an_executed_action_is_shown_and_downloadable():
    service = FakeService(_with_action())
    at = _submit(_app(service))
    assert not [b for b in at.button if b.key == "write_postmortem"]  # nothing ran yet
    at = at.button(key="approve_action").click().run()
    at = at.button(key="write_postmortem").click().run()
    assert not at.exception
    text = _all_text(at)
    assert "Checkout down 12 min" in text and "Alert on pool usage" in text and "INC-1042" in text
    assert any("saved to memory" in s.value.lower() for s in at.success)
    assert [a.action for a in service.postmortem_attempts] == ["rollback_payment_api"]


def test_postmortem_failure_is_shown():
    service = FakeService(_with_action(), postmortem_error=IncidentMemoryError("reflect down"))
    at = _submit(_app(service))
    at = at.button(key="approve_action").click().run()
    at = at.button(key="write_postmortem").click().run()
    assert any("reflect down" in e.value for e in at.error)


def _curve_file(tmp_path, baseline=True):
    def row(n, recall, first, wrong, engineer):
        return {"round": n, "incidents": 4, "relevant_recall": recall, "resolved": 4, "agent_fixed": first,
                "agent_first_try": first, "actions": 4 + wrong, "wrong_actions": wrong,
                "engineer_actions": engineer, "llm_errors": 0}
    report = {"generated_at": "2026-09-28T12:00:00Z", "bank_id": "shopfast-curve-1",
              "approvals": "given by the evaluation harness",
              "with_memory": [row(1, 0, 2, 2, 4), row(2, 4, 4, 0, 0), row(3, 4, 4, 0, 0)],
              "memory_off": [row(n, 0, 2, 2, 4) for n in (1, 2, 3)] if baseline else None}
    path = tmp_path / "learning_curve.json"
    path.write_text(json.dumps(report))
    return path


@pytest.mark.parametrize("baseline", [True, False])
def test_learned_tab_shows_learning_curve(tmp_path, baseline):
    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["service"] = FakeService()
    at.session_state["curve_results_path"] = str(_curve_file(tmp_path, baseline))
    at.run()
    assert not at.exception
    text = _all_text(at)
    assert "Round 1: memory recalled 0/4, first action fixed 2/4, wrong actions 2" in text
    assert "Round 3: memory recalled 4/4, first action fixed 4/4, wrong actions 0" in text


def test_learned_tab_explains_how_to_run_learning_curve_when_missing(tmp_path):
    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["service"] = FakeService()
    at.session_state["curve_results_path"] = str(tmp_path / "missing.json")
    at.run()
    assert "scripts.learning_curve" in _all_text(at)


@pytest.mark.parametrize("runbook, expected", [("| failure | fix |", "| failure | fix |"),
                                               (None, "No living runbook in this bank yet")])
def test_living_runbook_loads_on_request(runbook, expected):
    at = _app(FakeService(runbook=runbook))
    assert expected not in _all_text(at)  # not fetched on every rerun
    at = at.button(key="load_runbook").click().run()
    assert expected in _all_text(at)


def test_integrate_tab_shows_mcp_and_api_usage():
    text = _all_text(_app(FakeService()))
    assert "mcp_server.server" in text and "/incidents/detect" in text and "X-API-Key" in text


def test_learning_curve_shows_averages_and_range_across_repeats(tmp_path):
    row = {"round": 2, "incidents": 8.0, "relevant_recall": 7.67, "resolved": 8.0, "agent_fixed": 8.0,
           "agent_first_try": 7.33, "agent_first_try_min": 7, "agent_first_try_max": 8, "actions": 9.0,
           "wrong_actions": 0.67, "wrong_actions_min": 0, "wrong_actions_max": 1, "engineer_actions": 0.0,
           "llm_errors": 0.0}
    path = tmp_path / "curve.json"
    path.write_text(json.dumps({"repeats": 3, "bank_id": "shopfast-curve-2", "with_memory": [row], "memory_off": None}))
    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["service"] = FakeService()
    at.session_state["curve_results_path"] = str(path)
    at.run()
    text = _all_text(at)
    assert "Round 2: memory recalled 7.67/8, first action fixed 7.33/8 (range 7-8), wrong actions 0.67" in text
    assert "averaged over 3 runs" in text


# persistence

def test_open_incident_and_action_log_survive_a_page_refresh():
    service = FakeService(_with_action())
    at = _submit(_app(service))
    at = at.button(key="approve_action").click().run()
    refreshed = _app(FakeService())  # new browser session, same SQLite file
    assert not refreshed.exception
    text = _all_text(refreshed)
    assert "Connection pool exhausted (INC-1042)" in text
    assert "rollback_payment_api (chosen by agent)" in text


def test_can_switch_between_open_incidents():
    at = _submit(_app(FakeService()), title="Checkout failing")
    at = _submit(at, title="Login failing")
    first_id = next(i for i, (inc, _) in at.session_state["incidents"].items() if inc.title == "Checkout failing")
    at.selectbox(key="open_incident").select(first_id).run()
    assert at.session_state["last_id"] == first_id


# team rules

def test_suggestion_names_the_team_rules_it_followed():
    at = _submit(_app(FakeService(_suggestion(team_rules=["prefer-rollback-after-change"]))))
    assert "Team rules from memory the agent followed: prefer-rollback-after-change" in _all_text(at)


def test_team_rules_load_and_add():
    service = FakeService()
    at = _app(service)
    at = at.button(key="load_rules").click().run()
    assert "[20] prefer-rollback-after-change: Prefer rolling back" in _all_text(at)
    at.text_input(key="rule_name").input("no-deploys-during-sale")
    at.text_area(key="rule_content").input("Never propose a deploy during a flash sale.")
    at = at.button(key="add_rule").click().run()
    assert [r.name for r in service.rules] == ["prefer-rollback-after-change", "no-deploys-during-sale"]
    assert any("saved to memory" in s.value for s in at.success)


def test_invalid_team_rule_is_rejected():
    service = FakeService()
    at = _app(service)
    at.text_input(key="rule_name").input("Bad Name")
    at.text_area(key="rule_content").input("short")
    at = at.button(key="add_rule").click().run()
    assert any("Invalid rule" in e.value for e in at.error) and len(service.rules) == 1
