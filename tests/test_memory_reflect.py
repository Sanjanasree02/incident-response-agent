"""Postmortem (reflect), living runbook (mental model), multi-chunk recall and search against a fake Hindsight client."""

from types import SimpleNamespace

import pytest
from hindsight_client_api.models.chunk_data import ChunkData
from hindsight_client_api.models.recall_response import RecallResponse

from agent.memory import POSTMORTEM_SCHEMA, RUNBOOK_ID, IncidentMemoryError
from agent.models import Postmortem
from tests.test_memory import FakeHindsight, _fact, _incident, _memory

POSTMORTEM = {
    "summary": "Checkout failed for 12 minutes.",
    "impact": "All checkouts returned 503.",
    "root_cause": "Pool size halved by a deploy.",
    "timeline": ["14:08 alert", "14:20 rollback"],
    "what_went_well": ["Memory recalled INC-1042"],
    "what_went_wrong": ["Restarted pods first"],
    "action_items": ["Alert on pool usage"],
    "related_incidents": ["INC-1042", "see INC-1067", "INC-2001", "INC-1042", "not an id"],
}


class ReflectFake(FakeHindsight):
    def __init__(self, structured=POSTMORTEM, error_text=None, models=(), content="| failure | fix |", **kwargs):
        super().__init__(**kwargs)
        self._structured, self._error_text = structured, error_text
        self._models, self._content = list(models), content

    def reflect(self, bank_id, query, **kwargs):
        self._record("reflect", bank_id=bank_id, query=query, **kwargs)
        return SimpleNamespace(structured_output=self._structured, structured_output_error=self._error_text, text="")

    def list_mental_models(self, bank_id):
        self._record("list_mental_models", bank_id=bank_id)
        return SimpleNamespace(items=[SimpleNamespace(id=i) for i in self._models])

    def create_mental_model(self, bank_id, **kwargs):
        self.calls.append(("create_mental_model", dict(bank_id=bank_id, **kwargs)))

    def get_mental_model(self, bank_id, mental_model_id, **kwargs):
        self._record("get_mental_model", bank_id=bank_id, mental_model_id=mental_model_id, **kwargs)
        return SimpleNamespace(content=self._content)


def test_reflect_postmortem_sends_context_and_schema_and_cleans_related_ids():
    client = ReflectFake()
    postmortem = _memory(client).reflect_postmortem(_incident(), "Action log: rollback worked")
    [(name, kwargs)] = client.calls
    assert name == "reflect"
    assert kwargs["context"] == "Action log: rollback worked"
    assert kwargs["response_schema"] == POSTMORTEM_SCHEMA
    assert "INC-2001" in kwargs["query"]
    assert postmortem.incident_id == "INC-2001"
    assert postmortem.related_incidents == ["INC-1042", "INC-1067"]  # own ID, duplicates and non-IDs dropped
    assert postmortem.saved_to_memory is False


@pytest.mark.parametrize("structured", [None, {"summary": "only a summary"}])
def test_reflect_postmortem_without_usable_output_raises(structured):
    with pytest.raises(IncidentMemoryError):
        _memory(ReflectFake(structured=structured, error_text="schema mismatch")).reflect_postmortem(_incident(), "c")


def test_retain_postmortem_appends_to_the_incident_document():
    client = ReflectFake()
    postmortem = Postmortem(incident_id="INC-2001", **{**POSTMORTEM, "related_incidents": ["INC-1042"]})
    _memory(client).retain_postmortem(_incident(), postmortem)
    [(name, kwargs)] = client.calls
    assert name == "retain"
    assert kwargs["document_id"] == "INC-2001"
    assert kwargs["update_mode"] == "append"
    assert kwargs["content"] == postmortem.to_text()
    assert "Related past incidents: INC-1042" in kwargs["content"]


def test_postmortem_text_does_not_look_like_recorded_action_evidence():
    text = Postmortem(incident_id="INC-2001", **{**POSTMORTEM, "related_incidents": []}).to_text()
    assert "Fix steps that worked" not in text and "\n- " not in text


def test_ensure_runbook_creates_mental_model_once():
    client = ReflectFake()
    _memory(client).ensure_runbook()
    created = [kwargs for name, kwargs in client.calls if name == "create_mental_model"]
    assert created and created[0]["id"] == RUNBOOK_ID
    assert created[0]["trigger"] == {"refresh_after_consolidation": True}

    existing = ReflectFake(models=[RUNBOOK_ID])
    _memory(existing).ensure_runbook()
    assert [name for name, _ in existing.calls] == ["list_mental_models"]


def test_get_runbook_returns_content_or_none():
    assert _memory(ReflectFake()).get_runbook() is None
    assert _memory(ReflectFake(models=[RUNBOOK_ID], content="table")).get_runbook() == "table"


def test_recall_similar_joins_outcome_and_appended_postmortem_chunks():
    response = RecallResponse(
        results=[_fact("rolled back", "INC-1042", "c0"), _fact("pool alert", "INC-1042", "c1"),
                 _fact("rolled back again", "INC-1042", "c0")],
        chunks={"c0": ChunkData(id="c0", text="Outcome text", chunk_index=0),
                "c1": ChunkData(id="c1", text="Postmortem text", chunk_index=1)},
    )
    [similar] = _memory(FakeHindsight(response)).recall_similar(_incident())
    assert similar.source_text == "Outcome text\n\nPostmortem text"


def test_search_returns_distinct_fact_texts_up_to_limit():
    response = RecallResponse(results=[_fact("a", "INC-1"), _fact("a", "INC-1"), _fact("b"), _fact("c")])
    client = FakeHindsight(response)
    assert _memory(client).search("pool", limit=2) == ["a", "b"]
    assert client.calls[0][1]["types"] == ["world"]


@pytest.mark.parametrize("call", [
    lambda m: m.reflect_postmortem(_incident(), "c"),
    lambda m: m.ensure_runbook(),
    lambda m: m.get_runbook(),
    lambda m: m.search("q"),
], ids=["reflect", "ensure_runbook", "get_runbook", "search"])
def test_new_hindsight_errors_are_wrapped(call):
    with pytest.raises(IncidentMemoryError):
        call(_memory(ReflectFake(error=ConnectionError("down"))))


# team rules (directives)

from agent.models import TeamRule  # noqa: E402


class DirectiveFake(FakeHindsight):
    def __init__(self, directives=(), **kwargs):
        super().__init__(**kwargs)
        self.directives = list(directives)

    def list_directives(self, bank_id):
        self._record("list_directives", bank_id=bank_id)
        return SimpleNamespace(items=self.directives)

    def create_directive(self, bank_id, **kwargs):
        self.calls.append(("create_directive", dict(bank_id=bank_id, **kwargs)))
        self.directives.append(SimpleNamespace(is_active=True, **kwargs))


def _directive(name, priority, active=True):
    return SimpleNamespace(name=name, content=f"Rule text for {name}", priority=priority, is_active=active)


def test_team_rules_are_active_directives_highest_priority_first():
    client = DirectiveFake([_directive("low-rule", 1), _directive("off-rule", 50, active=False),
                            _directive("high-rule", 20)])
    assert [r.name for r in _memory(client).team_rules()] == ["high-rule", "low-rule"]


def test_ensure_team_rules_adds_only_missing_rules():
    client = DirectiveFake([_directive("existing-rule", 5)])
    rules = [TeamRule(name="existing-rule", content="Already stored rule text"),
             TeamRule(name="new-rule", content="A rule that is not stored yet", priority=30)]
    assert _memory(client).ensure_team_rules(rules) == ["new-rule"]
    [(name, kwargs)] = [c for c in client.calls if c[0] == "create_directive"]
    assert kwargs["name"] == "new-rule" and kwargs["priority"] == 30


def test_postmortem_reflect_applies_all_directives():
    client = ReflectFake()
    _memory(client).reflect_postmortem(_incident(), "c")
    assert client.calls[0][1]["apply_all_directives"] is True
