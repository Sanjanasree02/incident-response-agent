"""Stateful front door for the REST API and the MCP server.

Keeps open incidents by ID (incident, latest suggestion, action log, postmortem) in an IncidentStore, so a caller can
analyze an incident in one call and act on it, record its outcome or ask for a postmortem in later calls by ID only,
even after a restart.
"""

from threading import Lock

from agent.models import Incident, Outcome, Postmortem, RemediationAttempt, TeamRule
from agent.service import IncidentService
from agent.store import IncidentStore, OpenIncident

__all__ = ["IncidentDesk", "OpenIncident", "UnknownIncident"]


class UnknownIncident(KeyError):
    """The incident ID was never analyzed here."""


class IncidentDesk:
    def __init__(self, service: IncidentService, store: IncidentStore | None = None) -> None:
        self._service = service
        self._store = store or IncidentStore(":memory:")
        self._lock = Lock()  # one change at a time per desk, so two calls cannot interleave on one incident

    def _keep(self, item: OpenIncident) -> OpenIncident:
        self._store.save(item)
        return item

    def get(self, incident_id: str) -> OpenIncident:
        item = self._store.get(incident_id)
        if item is None:
            raise UnknownIncident(incident_id)
        return item

    def recent(self, limit: int = 20) -> list[OpenIncident]:
        return self._store.recent(limit)

    def detect(self) -> OpenIncident | None:
        """Probe ShopFast; if something fails, open and analyze an incident from its latest alert."""
        incident = self._service.detect_incident()
        return self.analyze(incident) if incident else None

    def analyze(self, incident: Incident) -> OpenIncident:
        return self._keep(OpenIncident(incident, self._service.analyze_incident(incident)))

    def reanalyze(self, incident_id: str) -> OpenIncident:
        """Ask again without the actions already tried for this incident."""
        with self._lock:
            item = self.get(incident_id)
            item.suggestion = self._service.analyze_incident(item.incident,
                                                             tried_actions=[a.action for a in item.attempts])
            return self._keep(item)

    def act(self, incident_id: str, action: str | None = None) -> RemediationAttempt:
        """Run the proposed action (or `action`, an engineer's choice). The caller is the human approver."""
        with self._lock:
            item = self.get(incident_id)
            attempt = self._service.remediate(item.incident, item.suggestion, approved=True,
                                              previous_attempts=list(item.attempts), action=action)
            item.attempts.append(attempt)
            self._keep(item)
            return attempt

    def record_outcome(self, incident_id: str, resolved: bool, actual_root_cause: str,
                       steps_that_worked: list[str], failed_attempts: list[str], notes: str = "") -> Outcome:
        item = self.get(incident_id)
        outcome = Outcome(incident_id=incident_id, resolved=resolved, actual_root_cause=actual_root_cause,
                          steps_that_worked=steps_that_worked, failed_attempts=failed_attempts, notes=notes)
        self._service.record_outcome(item.incident, outcome)
        return outcome

    def postmortem(self, incident_id: str) -> Postmortem:
        with self._lock:
            item = self.get(incident_id)
            item.postmortem = self._service.write_postmortem(item.incident, item.suggestion, item.attempts)
            self._keep(item)
            return item.postmortem

    def runbook(self) -> str | None:
        return self._service.runbook()

    def team_rules(self) -> list[TeamRule]:
        return self._service.team_rules()

    def add_team_rule(self, rule: TeamRule) -> None:
        self._service.add_team_rule(rule)

    def search(self, query: str, limit: int = 10) -> list[str]:
        return self._service.search_memory(query, limit)
