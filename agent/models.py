"""Data models for incidents, outcomes and agent suggestions. All user input is validated here."""

import secrets
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_LOG_CHARS = 8000
MAX_TEXT_CHARS = 2000
INCIDENT_ID_PATTERN = r"^INC-\d{4,16}$"
SERVICE_PATTERN = r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"

# One step or attempt in a list: not blank, bounded length.
Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS)]


_id_lock = threading.Lock()
_id_second = ""
_ids_this_second: set[int] = set()


def new_incident_id() -> str:
    """Generate an ID like INC-2609281430120734: UTC timestamp plus 4 random digits.

    The ID is the Hindsight document_id, so two incidents must never share one: within this process an ID is
    never repeated, and across processes a collision needs the same second and the same 1-in-10,000 suffix.
    """
    global _id_second
    with _id_lock:
        while True:
            stamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
            if stamp != _id_second:
                _id_second = stamp
                _ids_this_second.clear()
            if len(_ids_this_second) < 10_000:
                break
            time.sleep(0.01)  # all suffixes used this second: wait for the next one
        suffix = secrets.randbelow(10_000)
        while suffix in _ids_this_second:
            suffix = secrets.randbelow(10_000)
        _ids_this_second.add(suffix)
        return f"INC-{stamp}{suffix:04d}"


class Severity(str, Enum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"


class Incident(BaseModel):
    """A new incident submitted by an engineer. The ID is generated when not supplied."""

    # Strip before length checks, so whitespace cannot satisfy min_length.
    model_config = ConfigDict(str_strip_whitespace=True)

    incident_id: str = Field(default_factory=new_incident_id, pattern=INCIDENT_ID_PATTERN)
    service: str = Field(min_length=1, max_length=64, pattern=SERVICE_PATTERN)
    severity: Severity
    title: str = Field(min_length=3, max_length=200)
    symptoms: str = Field(min_length=3, max_length=MAX_TEXT_CHARS)
    error_log: str = Field(default="", max_length=MAX_LOG_CHARS)
    reported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Outcome(BaseModel):
    """What actually happened after the engineer worked on the incident."""

    model_config = ConfigDict(str_strip_whitespace=True)

    incident_id: str = Field(pattern=INCIDENT_ID_PATTERN)
    resolved: bool
    actual_root_cause: str = Field(min_length=3, max_length=MAX_TEXT_CHARS)
    steps_that_worked: list[Step] = Field(default_factory=list, max_length=20)
    failed_attempts: list[Step] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=MAX_TEXT_CHARS)


class HistoricalIncident(Incident):
    """A past incident with its resolution. Used for seed data."""

    reported_at: datetime  # required: a missing date must not default to now

    root_cause: str = Field(min_length=3, max_length=MAX_TEXT_CHARS)
    resolution_steps: list[Step] = Field(min_length=1, max_length=20)
    failed_attempts: list[Step] = Field(default_factory=list, max_length=20)
    lesson: str = Field(default="", max_length=MAX_TEXT_CHARS)


class SimilarIncident(BaseModel):
    """A past incident recalled from Hindsight memory, with its source reference."""

    incident_id: str
    summary: str
    source_text: str = ""


class LearnedPattern(BaseModel):
    """An observation Hindsight consolidated from many incidents, e.g. 'restarting pods never fixes pool exhaustion'."""

    text: str


class TeamRule(BaseModel):
    """A rule the team sets for the agent, stored in Hindsight as a directive (e.g. "prefer rollback after a deploy")."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    content: str = Field(min_length=10, max_length=500)
    priority: int = Field(default=10, ge=0, le=100)


class RemediationAction(BaseModel):
    """An allow-listed runbook action the agent may propose. Names come from ShopFast's /ops/actions."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=3, max_length=MAX_TEXT_CHARS)


class RemediationAttempt(BaseModel):
    """Audit record of one proposed action: what, why, whether a human approved it, and what verification saw."""

    action: str
    description: str
    reason: str = ""
    chosen_by: str = Field(default="agent", pattern=r"^(agent|engineer)$")
    approved: bool
    executed: bool
    verified: bool
    health: dict[str, int] = Field(default_factory=dict)  # e.g. {"POST /checkout": 200}
    error: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ShopFastAlert(BaseModel):
    """A failure ShopFast recorded (GET /ops/incidents/latest). Turned into an Incident by agent.intake."""

    id: int
    at: str = Field(max_length=40)
    method: str = Field(max_length=10)
    path: str = Field(max_length=200)
    status: int
    error: str = Field(max_length=MAX_TEXT_CHARS)
    log: str = Field(max_length=MAX_LOG_CHARS)
    count: int = Field(ge=1)


class ActionEvidence(BaseModel):
    """How a runbook action fared in outcomes the agent recorded after verification (read from memory, not guessed)."""

    action: str
    worked_in: list[str] = Field(default_factory=list)  # incident IDs where the action restored health
    failed_in: list[str] = Field(default_factory=list)  # incident IDs where checkout was still failing after it

    @property
    def latest(self) -> str:
        """Newest incident this evidence comes from (generated IDs sort by time)."""
        return max(self.worked_in + self.failed_in, key=lambda i: int(i.removeprefix("INC-")))


class Suggestion(BaseModel):
    """The agent's answer for a new incident."""

    similar_incidents: list[SimilarIncident] = Field(default_factory=list)
    learned_patterns: list[LearnedPattern] = Field(default_factory=list)
    probable_root_cause: str
    fix_steps: list[Step] = Field(default_factory=list)
    avoid_steps: list[Step] = Field(default_factory=list)
    confidence: str = Field(pattern=r"^(low|medium|high)$")
    memory_used: bool
    llm_error: str | None = None  # set when the LLM failed; recalled memory is still returned
    proposed_action: str | None = None  # allow-listed runbook action; runs only after human approval
    action_reason: str = ""
    action_evidence: list[ActionEvidence] = Field(default_factory=list)
    team_rules: list[str] = Field(default_factory=list)  # names of the team rules the advisor was given


TextItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS)]


class Postmortem(BaseModel):
    """Blameless postmortem written by Hindsight reflect over the incident and the whole memory bank."""

    incident_id: str = Field(pattern=INCIDENT_ID_PATTERN)
    summary: TextItem
    impact: TextItem
    root_cause: TextItem
    timeline: list[TextItem] = Field(default_factory=list, max_length=30)
    what_went_well: list[TextItem] = Field(default_factory=list, max_length=20)
    what_went_wrong: list[TextItem] = Field(default_factory=list, max_length=20)
    action_items: list[TextItem] = Field(default_factory=list, max_length=20)
    related_incidents: list[str] = Field(default_factory=list, max_length=20)
    saved_to_memory: bool = False

    def to_text(self) -> str:
        """Plain-text form: stored in memory and offered as a download."""
        lines = [f"Postmortem for incident {self.incident_id}", f"Summary: {self.summary}",
                 f"Impact: {self.impact}", f"Root cause: {self.root_cause}"]
        for heading, items in (("Timeline", self.timeline), ("What went well", self.what_went_well),
                               ("What went wrong", self.what_went_wrong), ("Action items", self.action_items)):
            if items:
                lines += [f"{heading}:", *(f"* {item}" for item in items)]
        if self.related_incidents:
            lines.append(f"Related past incidents: {', '.join(self.related_incidents)}")
        return "\n".join(lines)
