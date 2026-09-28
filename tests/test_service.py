"""IncidentService tests with fake memory and advisor."""

import pytest

from agent.llm import LLMError
from agent.memory import IncidentMemoryError
from agent.models import Incident, LearnedPattern, Outcome, SimilarIncident, Suggestion
from agent.service import IncidentService

SIMILAR = [SimilarIncident(incident_id="INC-1042", summary="Pool exhausted")]
PATTERNS = [LearnedPattern(text="Restarting pods never fixes pool exhaustion")]
INCIDENT = Incident(incident_id="INC-2001", service="payment-api", severity="SEV1",
                    title="Checkout failing", symptoms="503 on checkout")
OUTCOME = Outcome(incident_id="INC-2001", resolved=True, actual_root_cause="Pool exhausted")


class FakeMemory:
    def __init__(self, similar=SIMILAR, patterns=PATTERNS, similar_error=None, patterns_error=None):
        self.calls = []
        self._similar, self._patterns = similar, patterns
        self._similar_error, self._patterns_error = similar_error, patterns_error

    def recall_similar(self, incident):
        self.calls.append(("recall_similar", incident))
        if self._similar_error:
            raise self._similar_error
        return self._similar

    def recall_learned_patterns(self, incident):
        self.calls.append(("recall_learned_patterns", incident))
        if self._patterns_error:
            raise self._patterns_error
        return self._patterns

    def retain_outcome(self, incident, outcome):
        self.calls.append(("retain_outcome", incident, outcome))


class FakeAdvisor:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def suggest(self, incident, similar, patterns):
        self.calls.append((incident, similar, patterns))
        if self._error:
            raise self._error
        return Suggestion(similar_incidents=similar, learned_patterns=patterns, probable_root_cause="Pool",
                          fix_steps=["Roll back (INC-1042)"], confidence="high", memory_used=bool(similar))


def test_analyze_recalls_memory_then_asks_advisor():
    memory, advisor = FakeMemory(), FakeAdvisor()
    suggestion = IncidentService(memory, advisor).analyze_incident(INCIDENT)
    assert [c[0] for c in memory.calls] == ["recall_similar", "recall_learned_patterns"]
    assert advisor.calls == [(INCIDENT, SIMILAR, PATTERNS)]
    assert suggestion.similar_incidents == SIMILAR
    assert suggestion.learned_patterns == PATTERNS
    assert suggestion.llm_error is None


def test_analyze_continues_without_patterns_when_pattern_recall_fails():
    memory, advisor = FakeMemory(patterns_error=IncidentMemoryError("observations down")), FakeAdvisor()
    suggestion = IncidentService(memory, advisor).analyze_incident(INCIDENT)
    assert advisor.calls == [(INCIDENT, SIMILAR, [])]
    assert suggestion.learned_patterns == []


def test_analyze_raises_when_similar_recall_fails():
    memory, advisor = FakeMemory(similar_error=IncidentMemoryError("hindsight down")), FakeAdvisor()
    with pytest.raises(IncidentMemoryError):
        IncidentService(memory, advisor).analyze_incident(INCIDENT)
    assert advisor.calls == []


@pytest.mark.parametrize("similar", [SIMILAR, []], ids=["with-memory", "without-memory"])
def test_llm_failure_still_returns_recalled_memory(similar):
    memory, advisor = FakeMemory(similar=similar), FakeAdvisor(error=LLMError("groq down"))
    suggestion = IncidentService(memory, advisor).analyze_incident(INCIDENT)
    assert suggestion.similar_incidents == similar
    assert suggestion.learned_patterns == PATTERNS
    assert suggestion.llm_error == "groq down"
    assert suggestion.confidence == "low"
    assert suggestion.fix_steps == []
    assert suggestion.memory_used is bool(similar)


def test_record_outcome_retains_it():
    memory = FakeMemory()
    IncidentService(memory, FakeAdvisor()).record_outcome(INCIDENT, OUTCOME)
    assert memory.calls == [("retain_outcome", INCIDENT, OUTCOME)]


# act -> verify -> learn

from agent.actions import ShopFastError  # noqa: E402
from agent.models import RemediationAction, RemediationAttempt  # noqa: E402

ACTIONS = [
    RemediationAction(name="rollback_payment_api", description="Roll payment-api back to its previous release"),
    RemediationAction(name="restart_payment_api_pods", description="Restart all payment-api pods"),
]
HEALTHY = {"GET /products": 200, "POST /checkout": 200}
BROKEN = {"GET /products": 200, "POST /checkout": 503}


class FakeShop:
    def __init__(self, health=HEALTHY, list_error=None, run_error=None):
        self.ran = []
        self._health, self._list_error, self._run_error = health, list_error, run_error

    def list_actions(self):
        if self._list_error:
            raise self._list_error
        return ACTIONS

    def run_action(self, name):
        if self._run_error:
            raise self._run_error
        self.ran.append(name)

    def health_check(self):
        return self._health


class ActionAdvisor(FakeAdvisor):
    def suggest(self, incident, similar, patterns, actions=(), tried_actions=()):
        self.calls.append((incident, similar, patterns, list(actions), list(tried_actions)))
        return Suggestion(similar_incidents=similar, learned_patterns=patterns, probable_root_cause="Pool exhausted",
                          fix_steps=["Roll back (INC-1042)"], confidence="high", memory_used=True,
                          proposed_action="rollback_payment_api", action_reason="Rollback fixed INC-1042")


def _proposal(action="rollback_payment_api"):
    return Suggestion(probable_root_cause="Pool exhausted after deploy", confidence="high", memory_used=True,
                      proposed_action=action, action_reason="Rollback fixed INC-1042")


def _service(shop=None, memory=None):
    return IncidentService(memory or FakeMemory(), ActionAdvisor(), shop=shop or FakeShop())


def test_analyze_offers_shop_actions_and_tried_actions_to_advisor():
    advisor = ActionAdvisor()
    IncidentService(FakeMemory(), advisor, shop=FakeShop()).analyze_incident(INCIDENT, tried_actions=["x_action"])
    assert advisor.calls[0][3] == ACTIONS
    assert advisor.calls[0][4] == ["x_action"]


def test_analyze_still_advises_when_shopfast_is_unreachable():
    advisor = ActionAdvisor()
    shop = FakeShop(list_error=ShopFastError("down"))
    suggestion = IncidentService(FakeMemory(), advisor, shop=shop).analyze_incident(INCIDENT)
    assert advisor.calls[0][3] == []
    assert suggestion.probable_root_cause == "Pool exhausted"


def test_rejected_action_is_not_executed_or_recorded():
    shop, memory = FakeShop(), FakeMemory()
    attempt = _service(shop, memory).remediate(INCIDENT, _proposal(), approved=False)
    assert (attempt.approved, attempt.executed, attempt.verified) == (False, False, False)
    assert shop.ran == [] and memory.calls == []


def test_approved_action_runs_verifies_and_records_success():
    shop, memory = FakeShop(health=HEALTHY), FakeMemory()
    attempt = _service(shop, memory).remediate(INCIDENT, _proposal(), approved=True)
    assert shop.ran == ["rollback_payment_api"]
    assert (attempt.approved, attempt.executed, attempt.verified) == (True, True, True)
    assert attempt.health == HEALTHY
    assert attempt.reason == "Rollback fixed INC-1042"
    [(name, incident, outcome)] = memory.calls
    assert name == "retain_outcome" and incident == INCIDENT
    assert outcome.resolved is True
    assert outcome.actual_root_cause == "Pool exhausted after deploy"
    assert outcome.steps_that_worked == ["rollback_payment_api: Roll payment-api back to its previous release"]
    assert outcome.failed_attempts == []
    assert "automatically" in outcome.notes.lower() and "POST /checkout 200" in outcome.notes


def test_failed_verification_is_recorded_as_failed_attempt():
    shop, memory = FakeShop(health=BROKEN), FakeMemory()
    attempt = _service(shop, memory).remediate(INCIDENT, _proposal("restart_payment_api_pods"), approved=True)
    assert (attempt.executed, attempt.verified) == (True, False)
    outcome = memory.calls[0][2]
    assert outcome.resolved is False
    assert outcome.steps_that_worked == []
    assert outcome.failed_attempts == ["restart_payment_api_pods: Restart all payment-api pods "
                                       "(still failing: POST /checkout 503)"]


def test_outcome_accumulates_earlier_attempts_and_skips_rejected_ones():
    earlier = [
        RemediationAttempt(action="scale_out_payment_api", description="Add pods", reason="r",
                           approved=False, executed=False, verified=False),
        RemediationAttempt(action="restart_payment_api_pods", description="Restart all payment-api pods", reason="r",
                           approved=True, executed=True, verified=False, health=BROKEN),
    ]
    memory = FakeMemory()
    _service(FakeShop(health=HEALTHY), memory).remediate(INCIDENT, _proposal(), approved=True,
                                                         previous_attempts=earlier)
    outcome = memory.calls[0][2]
    assert outcome.resolved is True
    assert outcome.failed_attempts == ["restart_payment_api_pods: Restart all payment-api pods "
                                       "(still failing: POST /checkout 503)"]
    assert outcome.steps_that_worked == ["rollback_payment_api: Roll payment-api back to its previous release"]


def test_action_outside_allow_list_is_refused_before_running():
    shop = FakeShop()
    with pytest.raises(ValueError):
        _service(shop).remediate(INCIDENT, _proposal("drop_database"), approved=True)
    assert shop.ran == []


def test_suggestion_without_action_cannot_be_remediated():
    with pytest.raises(ValueError):
        _service().remediate(INCIDENT, _proposal(action=None), approved=True)


def test_shopfast_error_during_action_is_reported_not_recorded():
    memory = FakeMemory()
    attempt = _service(FakeShop(run_error=ShopFastError("connection refused")), memory).remediate(
        INCIDENT, _proposal(), approved=True)
    assert (attempt.executed, attempt.verified) == (False, False)
    assert "connection refused" in attempt.error
    assert memory.calls == []


class FailingRetainMemory(FakeMemory):
    def retain_outcome(self, incident, outcome):
        raise IncidentMemoryError("hindsight down")


def test_memory_failure_after_action_keeps_the_executed_attempt():
    shop = FakeShop(health=HEALTHY)
    attempt = _service(shop, FailingRetainMemory()).remediate(INCIDENT, _proposal(), approved=True)
    assert shop.ran == ["rollback_payment_api"]
    assert (attempt.executed, attempt.verified) == (True, True)
    assert "not recorded" in attempt.error and "hindsight down" in attempt.error


# learning evidence

RECORDED = SimilarIncident(
    incident_id="INC-26092810000001", summary="Pool exhausted",
    source_text="Fix steps that worked:\n- rollback_payment_api: Roll back\n"
                "Fix attempts that did not work:\n- restart_payment_api_pods: Restart (checkout still failing)")
UNRELATED = SimilarIncident(
    incident_id="INC-26092810000002", summary="Redis",
    source_text="Fix steps that worked:\n- restart_payment_api_pods: Restart")


class RelevantOnlyAdvisor(ActionAdvisor):
    """Judges only RECORDED relevant, like the real advisor filtering recall."""

    def suggest(self, incident, similar, patterns, actions=(), tried_actions=()):
        suggestion = super().suggest(incident, similar, patterns, actions, tried_actions)
        return suggestion.model_copy(update={"similar_incidents": [s for s in similar if s is RECORDED]})


def test_analyze_attaches_evidence_from_relevant_recorded_outcomes_only():
    memory = FakeMemory(similar=[RECORDED, UNRELATED])
    suggestion = IncidentService(memory, RelevantOnlyAdvisor(), shop=FakeShop()).analyze_incident(INCIDENT)
    evidence = {e.action: e for e in suggestion.action_evidence}
    assert evidence["rollback_payment_api"].worked_in == ["INC-26092810000001"]
    assert evidence["restart_payment_api_pods"].failed_in == ["INC-26092810000001"]
    assert evidence["restart_payment_api_pods"].worked_in == []  # UNRELATED was judged not relevant


def test_llm_failure_still_shows_evidence_from_recalled_memory():
    memory = FakeMemory(similar=[RECORDED])
    class DownAdvisor(ActionAdvisor):
        def suggest(self, *args, **kwargs):
            raise LLMError("down")

    service = IncidentService(memory, DownAdvisor(), shop=FakeShop())
    suggestion = service.analyze_incident(INCIDENT)
    assert [e.action for e in suggestion.action_evidence] == ["rollback_payment_api", "restart_payment_api_pods"]


def test_no_evidence_without_shopfast_allow_list():
    suggestion = IncidentService(FakeMemory(similar=[RECORDED]), FakeAdvisor()).analyze_incident(INCIDENT)
    assert suggestion.action_evidence == []


def test_engineer_can_run_an_action_the_agent_did_not_propose():
    shop, memory = FakeShop(health=HEALTHY), FakeMemory()
    attempt = _service(shop, memory).remediate(INCIDENT, _proposal(action=None), approved=True,
                                               action="rollback_payment_api")
    assert shop.ran == ["rollback_payment_api"]
    assert attempt.chosen_by == "engineer" and "engineer" in attempt.reason
    outcome = memory.calls[0][2]
    assert outcome.steps_that_worked == ["rollback_payment_api: Roll payment-api back to its previous release"]


def test_choosing_the_proposed_action_counts_as_the_agents_choice():
    attempt = _service().remediate(INCIDENT, _proposal(), approved=True, action="rollback_payment_api")
    assert attempt.chosen_by == "agent" and attempt.reason == "Rollback fixed INC-1042"


def test_engineer_choice_outside_allow_list_is_refused():
    shop = FakeShop()
    with pytest.raises(ValueError):
        _service(shop).remediate(INCIDENT, _proposal(), approved=True, action="drop_database")
    assert shop.ran == []


# postmortem, runbook, search

from agent.models import Postmortem  # noqa: E402
from agent.service import postmortem_context  # noqa: E402

PM = Postmortem(incident_id="INC-2001", summary="s", impact="i", root_cause="r")


class PostmortemMemory(FakeMemory):
    def __init__(self, retain_error=None, **kwargs):
        super().__init__(**kwargs)
        self._retain_error = retain_error

    def reflect_postmortem(self, incident, context):
        self.calls.append(("reflect_postmortem", incident, context))
        return PM

    def retain_postmortem(self, incident, postmortem):
        self.calls.append(("retain_postmortem", incident, postmortem))
        if self._retain_error:
            raise self._retain_error

    def get_runbook(self):
        return "runbook table"

    def search(self, query, limit):
        return [f"{query}:{limit}"]


def _attempts():
    return [
        RemediationAttempt(action="restart_payment_api_pods", description="Restart", reason="r", approved=True,
                           executed=True, verified=False, health=BROKEN),
        RemediationAttempt(action="scale_out_payment_api", description="Add pods", approved=False, executed=False,
                           verified=False),
        RemediationAttempt(action="rollback_payment_api", description="Roll back", chosen_by="engineer",
                           approved=True, executed=True, verified=True, health=HEALTHY),
    ]


def test_postmortem_context_lists_diagnosis_memory_and_every_decision():
    suggestion = Suggestion(similar_incidents=SIMILAR, probable_root_cause="Pool exhausted", confidence="high",
                            memory_used=True)
    text = postmortem_context(INCIDENT, suggestion, _attempts())
    assert "Incident INC-2001 (SEV1)" in text and "Pool exhausted" in text and "INC-1042" in text
    assert "restart_payment_api_pods (Restart), chosen by agent: still failing: POST /checkout 503" in text
    assert "scale_out_payment_api (Add pods), chosen by agent: rejected, not run" in text
    assert "rollback_payment_api (Roll back), chosen by engineer: shop healthy afterwards" in text


def test_postmortem_context_without_actions_says_so():
    suggestion = Suggestion(probable_root_cause="?", confidence="low", memory_used=False)
    assert "no action was taken" in postmortem_context(INCIDENT, suggestion, [])


def test_write_postmortem_reflects_then_appends_to_memory():
    memory = PostmortemMemory()
    suggestion = Suggestion(probable_root_cause="Pool", confidence="high", memory_used=True)
    postmortem = IncidentService(memory, FakeAdvisor()).write_postmortem(INCIDENT, suggestion, _attempts())
    assert [c[0] for c in memory.calls] == ["reflect_postmortem", "retain_postmortem"]
    assert postmortem.saved_to_memory is True


def test_write_postmortem_still_returned_when_storing_fails():
    memory = PostmortemMemory(retain_error=IncidentMemoryError("down"))
    suggestion = Suggestion(probable_root_cause="Pool", confidence="high", memory_used=True)
    postmortem = IncidentService(memory, FakeAdvisor()).write_postmortem(INCIDENT, suggestion, [])
    assert postmortem.saved_to_memory is False and postmortem.summary == "s"


def test_runbook_and_search_pass_through_memory():
    service = IncidentService(PostmortemMemory(), FakeAdvisor())
    assert service.runbook() == "runbook table"
    assert service.search_memory("pool", 3) == ["pool:3"]
