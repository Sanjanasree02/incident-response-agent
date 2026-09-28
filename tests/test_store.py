"""SQLite store for open incidents."""

from agent.models import Incident, Postmortem, RemediationAttempt, Suggestion
from agent.store import IncidentStore, OpenIncident


def _item(incident_id="INC-2001", attempts=()):
    incident = Incident(incident_id=incident_id, service="payment-api", severity="SEV1", title="Checkout failing",
                        symptoms="503 on checkout")
    suggestion = Suggestion(probable_root_cause="Pool", confidence="high", memory_used=True,
                            proposed_action="rollback_payment_api")
    return OpenIncident(incident, suggestion, list(attempts))


def test_round_trip_with_attempts_and_postmortem(tmp_path):
    store = IncidentStore(tmp_path / "db" / "incidents.db")  # parent folder is created
    attempt = RemediationAttempt(action="rollback_payment_api", description="Roll back", approved=True,
                                 executed=True, verified=True, health={"POST /checkout": 200})
    item = _item(attempts=[attempt])
    item.postmortem = Postmortem(incident_id="INC-2001", summary="s", impact="i", root_cause="r")
    store.save(item)
    loaded = store.get("INC-2001")
    assert loaded.incident == item.incident and loaded.suggestion == item.suggestion
    assert loaded.attempts == [attempt] and loaded.resolved
    assert loaded.postmortem == item.postmortem


def test_save_updates_in_place_and_recent_is_newest_first():
    store = IncidentStore(":memory:")
    store.save(_item("INC-2001"))
    store.save(_item("INC-2002"))
    store.save(_item("INC-2001"))  # updated again: now the newest
    assert [i.incident.incident_id for i in store.recent()] == ["INC-2001", "INC-2002"]
    assert len(store.recent(limit=1)) == 1


def test_unknown_incident_is_none():
    assert IncidentStore(":memory:").get("INC-9999") is None
