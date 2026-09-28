import pytest

from agent.memory import IncidentMemoryError
from scripts import seed_memory
from scripts.seed_memory import load_seed_incidents, seed


class FakeMemory:
    bank_id = "shopfast-incidents-test"

    def __init__(self, fail_ids=(), bank_error=None):
        self.calls = []
        self.closed = False
        self._fail_ids = set(fail_ids)
        self._bank_error = bank_error

    def ensure_bank(self):
        self.calls.append("ensure_bank")
        if self._bank_error:
            raise self._bank_error

    def ensure_runbook(self):
        self.calls.append("ensure_runbook")

    def ensure_team_rules(self, rules):
        self.calls.append("ensure_team_rules")
        self.rules = rules
        return [r.name for r in rules]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def retain_historical(self, incident):
        self.calls.append(incident.incident_id)
        if incident.incident_id in self._fail_ids:
            raise IncidentMemoryError(f"retain failed for {incident.incident_id}")


def test_seed_creates_bank_first_then_retains_every_incident_in_order():
    incidents = load_seed_incidents()
    memory = FakeMemory()
    assert seed(memory, incidents) == []
    assert memory.calls == ["ensure_bank", *(i.incident_id for i in incidents), "ensure_team_rules", "ensure_runbook"]
    assert {r.name for r in memory.rules} >= {"prefer-rollback-after-change", "blameless-postmortems"}


def test_seed_continues_after_a_failure_and_reports_failed_ids():
    incidents = load_seed_incidents()
    memory = FakeMemory(fail_ids={"INC-1042", "INC-1156"})
    assert seed(memory, incidents) == ["INC-1042", "INC-1156"]
    assert len(memory.calls) == 3 + len(incidents)


def test_seed_stops_when_bank_cannot_be_created():
    memory = FakeMemory(bank_error=IncidentMemoryError("bad key"))
    with pytest.raises(IncidentMemoryError):
        seed(memory, load_seed_incidents())
    assert memory.calls == ["ensure_bank"]


@pytest.mark.parametrize("fail_ids, exit_code", [((), 0), (("INC-1042",), 1)])
def test_main_uses_bank_override_and_exit_code(monkeypatch, fail_ids, exit_code):
    seen = {}

    def fake_load_settings(bank_id_override=None):
        seen["bank"] = bank_id_override
        return object()

    monkeypatch.setattr(seed_memory, "load_settings", fake_load_settings)
    memory = FakeMemory(fail_ids=fail_ids)
    monkeypatch.setattr(seed_memory, "IncidentMemory", lambda settings: memory)
    assert seed_memory.main(["--bank-id", "shopfast-incidents-demo3"]) == exit_code
    assert seen["bank"] == "shopfast-incidents-demo3"
    assert memory.closed


def test_main_closes_memory_when_bank_creation_fails(monkeypatch):
    memory = FakeMemory(bank_error=IncidentMemoryError("bad key"))
    monkeypatch.setattr(seed_memory, "load_settings", lambda bank_id_override=None: object())
    monkeypatch.setattr(seed_memory, "IncidentMemory", lambda settings: memory)
    with pytest.raises(IncidentMemoryError):
        seed_memory.main([])
    assert memory.closed
