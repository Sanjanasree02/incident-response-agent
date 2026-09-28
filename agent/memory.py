"""Thin wrapper around the Hindsight client. All memory reads and writes go through this module."""

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from hindsight_client import Hindsight

from agent.config import Settings
from agent.log_normalizer import normalize_log
from agent.models import (HistoricalIncident, Incident, LearnedPattern, Outcome, Postmortem, SimilarIncident,
                          TeamRule)

BANK_MISSION = (
    "You are the incident memory for ShopFast, an e-commerce platform. "
    "Remember incidents, root causes, fixes that worked and fixes that failed."
)
RETAIN_MISSION = (
    "Extract each incident's ID, service, symptoms, error signature, root cause, the fix steps that worked "
    "and the fix attempts that did not work. Always keep the incident ID with each fact."
)

RUNBOOK_ID = "shopfast-runbook"
RUNBOOK_NAME = "ShopFast living runbook"
RUNBOOK_QUERY = (
    "For each kind of ShopFast failure seen so far (error signature and service): which runbook actions or fix "
    "steps worked, which failed, and the lesson. Cite incident IDs. Use a table."
)
POSTMORTEM_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "impact": {"type": "string"},
        "root_cause": {"type": "string"},
        "timeline": {"type": "array", "items": {"type": "string"}},
        "what_went_well": {"type": "array", "items": {"type": "string"}},
        "what_went_wrong": {"type": "array", "items": {"type": "string"}},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "related_incidents": {"type": "array", "items": {"type": "string"},
                              "description": "IDs of past incidents in memory with the same failure"},
    },
    "required": ["summary", "impact", "root_cause", "timeline", "what_went_well", "what_went_wrong",
                 "action_items", "related_incidents"],
}

# "world" = stored incident facts; "observation" = patterns Hindsight consolidates across incidents.
INCIDENT_FACT_TYPES = ["world"]
LEARNED_PATTERN_TYPES = ["observation"]

_INCIDENT_ID_IN_TEXT = re.compile(r"\bINC-\d{4,16}\b")
T = TypeVar("T")


class IncidentMemoryError(RuntimeError):
    """Raised when a Hindsight call fails. The message names the operation and the bank."""


def build_recall_query(incident: Incident) -> str:
    """Build the recall query from the incident. The log is normalized so noise does not hurt matching."""
    return f"{incident.service}: {incident.title}. {incident.symptoms}. {normalize_log(incident.error_log)}"


def _incident_header(incident: Incident) -> list[str]:
    lines = [
        f"Incident {incident.incident_id} ({incident.severity.value}) in service {incident.service}: {incident.title}",
        f"Symptoms: {incident.symptoms}",
    ]
    if incident.error_log:
        lines.append(f"Error log: {normalize_log(incident.error_log)}")
    return lines


def _step_lines(heading: str, steps: list[str]) -> list[str]:
    return [heading, *(f"- {step}" for step in steps)] if steps else []


def _metadata(incident: Incident, resolved: bool) -> dict[str, str]:
    return {"service": incident.service, "severity": incident.severity.value, "resolved": str(resolved).lower()}


def format_historical(incident: HistoricalIncident) -> str:
    """Text stored for a seeded past incident. The incident ID is in the text so recalled facts can cite it."""
    lines = _incident_header(incident)
    lines.append(f"Root cause: {incident.root_cause}")
    lines += _step_lines("Fix steps that worked:", incident.resolution_steps)
    lines += _step_lines("Fix attempts that did not work:", incident.failed_attempts)
    if incident.lesson:
        lines.append(f"Lesson: {incident.lesson}")
    return "\n".join(lines)


def format_outcome(incident: Incident, outcome: Outcome) -> str:
    """Text stored for a newly recorded incident outcome."""
    lines = _incident_header(incident)
    lines.append("Status: resolved" if outcome.resolved else "Status: not resolved")
    lines.append(f"Root cause: {outcome.actual_root_cause}")
    lines += _step_lines("Fix steps that worked:", outcome.steps_that_worked)
    lines += _step_lines("Fix attempts that did not work:", outcome.failed_attempts)
    if outcome.notes:
        lines.append(f"Notes: {outcome.notes}")
    return "\n".join(lines)


class IncidentMemory:
    def __init__(self, settings: Settings, client: Hindsight | None = None) -> None:
        self._bank_id = settings.hindsight_bank_id
        self._client = client or Hindsight(
            base_url=settings.hindsight_base_url,
            api_key=settings.hindsight_api_key,
        )
        # The SDK's sync methods run on the calling thread's event loop, and its aiohttp session is bound to
        # the first loop it runs on. Callers such as Streamlit reruns come from different threads, so every
        # client call runs on this one thread, which keeps one event loop for the client's whole life.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight")

    @property
    def bank_id(self) -> str:
        return self._bank_id

    def close(self) -> None:
        """Close the Hindsight client's HTTP session. Call when done, or use `with IncidentMemory(...)`."""
        try:
            self._worker.submit(self._client.close).result()
        finally:
            self._worker.shutdown()

    def __enter__(self) -> "IncidentMemory":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _call(self, operation: str, fn: Callable[[], T]) -> T:
        try:
            return self._worker.submit(fn).result()
        except Exception as exc:
            raise IncidentMemoryError(f"Hindsight {operation} failed for bank {self._bank_id!r}: {exc}") from exc

    def ensure_bank(self) -> None:
        """Create the memory bank, or update its settings if it already exists (Hindsight create is an upsert)."""
        self._call("create_bank", lambda: self._client.create_bank(
            self._bank_id,
            reflect_mission=BANK_MISSION,
            retain_mission=RETAIN_MISSION,
            enable_observations=True,
        ))

    def retain_historical(self, incident: HistoricalIncident) -> None:
        """Store one seeded past incident. Uses incident_id as document_id."""
        self._retain(incident, format_historical(incident), resolved=True)

    def retain_outcome(self, incident: Incident, outcome: Outcome) -> None:
        """Store a newly resolved (or failed) incident so future recalls can use it."""
        if outcome.incident_id != incident.incident_id:
            raise ValueError(f"Outcome is for {outcome.incident_id}, not {incident.incident_id}")
        self._retain(incident, format_outcome(incident, outcome), resolved=outcome.resolved)

    def _retain(self, incident: Incident, content: str, resolved: bool) -> None:
        self._call("retain", lambda: self._client.retain(
            self._bank_id,
            content,
            timestamp=incident.reported_at,
            context="ShopFast production incident",
            document_id=incident.incident_id,
            metadata=_metadata(incident, resolved),
        ))

    def recall_similar(self, incident: Incident, limit: int = 5) -> list[SimilarIncident]:
        """Return past incidents similar to the given one, with source references, in rank order."""
        response = self._call("recall", lambda: self._client.recall(
            self._bank_id,
            build_recall_query(incident),
            types=INCIDENT_FACT_TYPES,
            include_chunks=True,
        ))
        chunks = response.chunks or {}
        facts: dict[str, list[str]] = {}
        # An incident's document can hold several chunks: the recorded outcome and, appended later, its postmortem.
        sources: dict[str, list[str]] = {}
        for result in response.results:
            incident_id = result.document_id or _first_incident_id(result.text)
            if not incident_id or incident_id == incident.incident_id:
                continue
            if incident_id not in facts and len(facts) == limit:
                continue
            facts.setdefault(incident_id, []).append(result.text)
            chunk = chunks.get(result.chunk_id) if result.chunk_id else None
            if chunk and chunk.text not in sources.setdefault(incident_id, []):
                sources[incident_id].append(chunk.text)
        return [
            SimilarIncident(incident_id=i, summary=" ".join(texts), source_text="\n\n".join(sources.get(i, [])))
            for i, texts in facts.items()
        ]

    def recall_learned_patterns(self, incident: Incident, limit: int = 5) -> list[LearnedPattern]:
        """Return patterns Hindsight learned across many incidents (observations) relevant to this one."""
        response = self._call("recall", lambda: self._client.recall(
            self._bank_id,
            build_recall_query(incident),
            types=LEARNED_PATTERN_TYPES,
        ))
        texts = list(dict.fromkeys(result.text for result in response.results))
        return [LearnedPattern(text=text) for text in texts[:limit]]


    def search(self, query: str, limit: int = 10) -> list[str]:
        """Raw incident facts for a free-text question, for tools such as the MCP server."""
        response = self._call("recall", lambda: self._client.recall(self._bank_id, query, types=INCIDENT_FACT_TYPES))
        return list(dict.fromkeys(result.text for result in response.results))[:limit]

    def reflect_postmortem(self, incident: Incident, context: str) -> Postmortem:
        """Ask Hindsight reflect to write a blameless postmortem, reasoning over this incident and the whole bank."""
        query = (f"Write a blameless postmortem for incident {incident.incident_id}. Use the incident record in the "
                 "context and past incidents in memory: find related incidents with the same failure, say whether "
                 "this failure has happened before and what was learned, and propose concrete action items.")
        response = self._call("reflect", lambda: self._client.reflect(
            self._bank_id, query, budget="mid", context=context, max_tokens=2000, response_schema=POSTMORTEM_SCHEMA,
            apply_all_directives=True,  # team rules such as "postmortems are blameless" shape the postmortem too
        ))
        data = response.structured_output
        if not isinstance(data, dict):
            raise IncidentMemoryError(f"Hindsight reflect returned no postmortem for bank {self._bank_id!r}: "
                                      f"{response.structured_output_error or 'empty output'}")
        cited = _INCIDENT_ID_IN_TEXT.findall(" ".join(map(str, data.get("related_incidents") or [])))
        related = [i for i in dict.fromkeys(cited) if i != incident.incident_id]
        try:
            return Postmortem.model_validate({**data, "incident_id": incident.incident_id,
                                              "related_incidents": related})
        except ValueError as exc:
            raise IncidentMemoryError(f"Hindsight reflect returned an invalid postmortem: {exc}") from exc

    def retain_postmortem(self, incident: Incident, postmortem: Postmortem) -> None:
        """Append the postmortem to the incident's document, so later recalls of the incident include its lessons."""
        self._call("retain", lambda: self._client.retain(
            self._bank_id,
            postmortem.to_text(),
            timestamp=incident.reported_at,
            context="ShopFast incident postmortem",
            document_id=incident.incident_id,
            metadata={"service": incident.service, "severity": incident.severity.value, "kind": "postmortem"},
            update_mode="append",
        ))

    def team_rules(self) -> list[TeamRule]:
        """Active team rules (Hindsight directives), highest priority first."""
        response = self._call("list_directives", lambda: self._client.list_directives(self._bank_id))
        rules = [TeamRule(name=d.name, content=d.content, priority=d.priority or 0)
                 for d in response.items if d.is_active]
        return sorted(rules, key=lambda r: -r.priority)

    def add_team_rule(self, rule: TeamRule) -> None:
        self._call("create_directive", lambda: self._client.create_directive(
            self._bank_id, name=rule.name, content=rule.content, priority=rule.priority))

    def ensure_team_rules(self, rules: list[TeamRule]) -> list[str]:
        """Create the rules whose names are not in the bank yet. Returns the names created."""
        existing = {r.name for r in self.team_rules()}
        created = []
        for rule in rules:
            if rule.name not in existing:
                self.add_team_rule(rule)
                created.append(rule.name)
        return created

    def _has_runbook(self) -> bool:
        existing = self._call("list_mental_models", lambda: self._client.list_mental_models(self._bank_id))
        return any(model.id == RUNBOOK_ID for model in existing.items)

    def ensure_runbook(self) -> None:
        """Create the living runbook: a Hindsight mental model that re-writes itself after each consolidation."""
        if self._has_runbook():
            return
        self._call("create_mental_model", lambda: self._client.create_mental_model(
            self._bank_id, name=RUNBOOK_NAME, source_query=RUNBOOK_QUERY, id=RUNBOOK_ID, max_tokens=1500,
            trigger={"refresh_after_consolidation": True},
        ))

    def get_runbook(self) -> str | None:
        """The living runbook's current text, or None when it was not created yet."""
        if not self._has_runbook():
            return None
        model = self._call("get_mental_model", lambda: self._client.get_mental_model(
            self._bank_id, RUNBOOK_ID, detail="content"))
        return model.content or ""


def _first_incident_id(text: str) -> str | None:
    match = _INCIDENT_ID_IN_TEXT.search(text)
    return match.group(0) if match else None
