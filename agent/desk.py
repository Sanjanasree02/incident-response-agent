"""Stateful front door for the REST API and the MCP server.

Remembers open incidents by ID (incident, latest suggestion, action log), so a caller can analyze an incident in one
call and act on it, record its outcome or ask for a postmortem in later calls by ID only.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock

from agent.models import Incident, Outcome, Postmortem, RemediationAttempt, Suggestion
from agent.service import IncidentService

MAX_OPEN_INCIDENTS = 200


class UnknownIncident(KeyError):
    """The incident ID was never analyzed here, or it was dropped to keep the store bounded."""


@dataclass
class OpenIncident:
    incident: Incident
    suggestion: Suggestion
    attempts: list[RemediationAttempt] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return any(a.verified for a in self.attempts)


class IncidentDesk:
    def __init__(self, service: IncidentService, max_open: int = MAX_OPEN_INCIDENTS) -> None:
        self._service = service
        self._open: OrderedDict[str, OpenIncident] = OrderedDict()
        self._max_open = max_open
        self._lock = Lock()

    def _keep(self, item: OpenIncident) -> OpenIncident:
        with self._lock:
            self._open[item.incident.incident_id] = item
            self._open.move_to_end(item.incident.incident_id)
            while len(self._open) > self._max_open:
                self._open.popitem(last=False)
        return item

    def get(self, incident_id: str) -> OpenIncident:
        with self._lock:
            item = self._open.get(incident_id)
        if item is None:
            raise UnknownIncident(incident_id)
        return item

    def detect(self) -> OpenIncident | None:
        """Probe ShopFast; if something fails, open and analyze an incident from its latest alert."""
        incident = self._service.detect_incident()
        return self.analyze(incident) if incident else None

    def analyze(self, incident: Incident) -> OpenIncident:
        return self._keep(OpenIncident(incident, self._service.analyze_incident(incident)))

    def reanalyze(self, incident_id: str) -> OpenIncident:
        """Ask again without the actions already tried for this incident."""
        item = self.get(incident_id)
        item.suggestion = self._service.analyze_incident(item.incident, tried_actions=[a.action for a in item.attempts])
        return item

    def act(self, incident_id: str, action: str | None = None) -> RemediationAttempt:
        """Run the proposed action (or `action`, an engineer's choice). The caller is the human approver."""
        item = self.get(incident_id)
        attempt = self._service.remediate(item.incident, item.suggestion, approved=True,
                                          previous_attempts=list(item.attempts), action=action)
        item.attempts.append(attempt)
        return attempt

    def record_outcome(self, incident_id: str, resolved: bool, actual_root_cause: str,
                       steps_that_worked: list[str], failed_attempts: list[str], notes: str = "") -> Outcome:
        item = self.get(incident_id)
        outcome = Outcome(incident_id=incident_id, resolved=resolved, actual_root_cause=actual_root_cause,
                          steps_that_worked=steps_that_worked, failed_attempts=failed_attempts, notes=notes)
        self._service.record_outcome(item.incident, outcome)
        return outcome

    def postmortem(self, incident_id: str) -> Postmortem:
        item = self.get(incident_id)
        return self._service.write_postmortem(item.incident, item.suggestion, item.attempts)

    def runbook(self) -> str | None:
        return self._service.runbook()

    def search(self, query: str, limit: int = 10) -> list[str]:
        return self._service.search_memory(query, limit)
