import re

import pytest
from pydantic import ValidationError

from agent.log_normalizer import normalize_log
from agent.models import (
    INCIDENT_ID_PATTERN,
    MAX_LOG_CHARS,
    MAX_TEXT_CHARS,
    HistoricalIncident,
    Incident,
    Outcome,
    Suggestion,
)
from scripts.seed_memory import load_seed_incidents
from shopfast.faults import FAULT_LOGS, Fault, FaultRegistry


def _incident(**overrides) -> dict:
    data = {
        "incident_id": "INC-2001",
        "service": "payment-api",
        "severity": "SEV1",
        "title": "Checkout failing",
        "symptoms": "503 on checkout",
        "error_log": "psycopg2.OperationalError",
    }
    data.update(overrides)
    return data


def test_valid_incident_parses():
    assert Incident.model_validate(_incident()).service == "payment-api"


def test_incident_id_generated_when_missing():
    data = _incident()
    del data["incident_id"]
    incident = Incident.model_validate(data)
    assert re.fullmatch(INCIDENT_ID_PATTERN, incident.incident_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"incident_id": "1234"},
        {"service": "Payment API; DROP"},
        {"severity": "SEV9"},
        {"error_log": "x" * (MAX_LOG_CHARS + 1)},
    ],
)
def test_invalid_incident_rejected(overrides):
    with pytest.raises(ValidationError):
        Incident.model_validate(_incident(**overrides))


def test_seed_file_is_valid_and_has_no_payment_gateway_history():
    incidents = load_seed_incidents()
    assert len(incidents) == 25
    assert len({i.incident_id for i in incidents}) == 25
    assert not any("paygate" in i.error_log.lower() for i in incidents)


def test_fault_registry_toggle():
    registry = FaultRegistry()
    registry.enable(Fault.REDIS_TIMEOUT)
    assert registry.is_active(Fault.REDIS_TIMEOUT)
    registry.disable(Fault.REDIS_TIMEOUT)
    assert registry.active() == []


@pytest.mark.parametrize("field", ["title", "symptoms"])
@pytest.mark.parametrize("value", ["     ", "  ab  "])
def test_incident_text_too_short_after_strip_rejected(field, value):
    with pytest.raises(ValidationError):
        Incident.model_validate(_incident(**{field: value}))


def test_outcome_whitespace_root_cause_rejected():
    with pytest.raises(ValidationError):
        Outcome.model_validate({"incident_id": "INC-2001", "resolved": True, "actual_root_cause": "     "})


def test_historical_incident_requires_reported_at():
    record = load_seed_incidents()[0].model_dump(exclude={"reported_at"})
    with pytest.raises(ValidationError):
        HistoricalIncident.model_validate(record)


def _outcome(**overrides) -> dict:
    data = {"incident_id": "INC-2001", "resolved": True, "actual_root_cause": "Pool too small"}
    data.update(overrides)
    return data


@pytest.mark.parametrize("field", ["steps_that_worked", "failed_attempts"])
@pytest.mark.parametrize("item", ["", "   ", "x" * (MAX_TEXT_CHARS + 1)], ids=["empty", "blank", "too-long"])
def test_outcome_bad_list_item_rejected(field, item):
    with pytest.raises(ValidationError):
        Outcome.model_validate(_outcome(**{field: ["Rolled back", item]}))


@pytest.mark.parametrize("item", ["   ", "x" * (MAX_TEXT_CHARS + 1)], ids=["blank", "too-long"])
def test_historical_bad_resolution_step_rejected(item):
    record = load_seed_incidents()[0].model_dump()
    record["resolution_steps"] = [item]
    with pytest.raises(ValidationError):
        HistoricalIncident.model_validate(record)


@pytest.mark.parametrize("service", ["-", "---", "-payment", "payment-"])
def test_service_must_start_and_end_with_letter_or_digit(service):
    with pytest.raises(ValidationError):
        Incident.model_validate(_incident(service=service))


def test_text_is_stripped():
    incident = Incident.model_validate(_incident(title="  Checkout failing  ", error_log="  boom \n"))
    assert incident.title == "Checkout failing"
    assert incident.error_log == "boom"


def test_generated_ids_are_valid_and_distinct():
    ids = {Incident.model_validate({k: v for k, v in _incident().items() if k != "incident_id"}).incident_id
           for _ in range(20)}
    assert all(re.fullmatch(INCIDENT_ID_PATTERN, i) for i in ids)
    assert len(ids) > 1


@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
def test_suggestion_accepts_confidence(confidence):
    Suggestion(probable_root_cause="x", confidence=confidence, memory_used=True)


def test_suggestion_rejects_unknown_confidence():
    with pytest.raises(ValidationError):
        Suggestion(probable_root_cause="x", confidence="certain", memory_used=True)


def test_seed_has_patterns_described_in_design():
    ids = {i.incident_id for i in load_seed_incidents()}
    for pattern in (
        {"INC-1042", "INC-1067", "INC-1113"},  # DB connection pool exhaustion
        {"INC-1051", "INC-1088", "INC-1129"},  # Redis timeout
        {"INC-1075", "INC-1098", "INC-1141"},  # Auth token expiry
    ):
        assert pattern <= ids


def test_seed_logs_have_no_timestamps_left_after_normalizing():
    for incident in load_seed_incidents():
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", normalize_log(incident.error_log)), incident.incident_id


def test_every_fault_has_a_log_line():
    assert set(FAULT_LOGS) == set(Fault)


def test_incident_ids_never_collide_within_the_same_second():
    # Two incidents opened in the same second must not share an ID: the ID is the Hindsight document_id,
    # so a collision would overwrite another incident's memory.
    from agent.models import new_incident_id

    ids = [new_incident_id() for _ in range(2000)]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(INCIDENT_ID_PATTERN, i) for i in ids)
