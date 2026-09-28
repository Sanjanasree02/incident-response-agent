"""Orchestrates the incident flow: detect, recall, suggest, act (with approval), verify, learn."""

from collections.abc import Sequence

from agent.actions import ShopFastClient, ShopFastError
from agent.evidence import action_evidence
from agent.intake import incident_from_alert
from agent.llm import IncidentAdvisor, LLMError
from agent.memory import IncidentMemory, IncidentMemoryError
from agent.models import Incident, Outcome, Postmortem, RemediationAction, RemediationAttempt, Suggestion

LLM_UNAVAILABLE = "AI suggestion unavailable. Review the similar past incidents below."
ENGINEER_CHOICE = "Chosen by the on-call engineer instead of the agent's proposal"


def _is_healthy(health: dict[str, int]) -> bool:
    return bool(health) and all(status < 400 for status in health.values())


def _failing(health: dict[str, int]) -> str:
    return ", ".join(f"{endpoint} {status}" for endpoint, status in health.items() if status >= 400)


def build_outcome(incident: Incident, suggestion: Suggestion, attempts: Sequence[RemediationAttempt]) -> Outcome:
    """The outcome the agent records after verifying its latest action. Rejected actions never ran, so they are
    left out; executed actions that did not restore health are failed attempts."""
    executed = [a for a in attempts if a.executed]
    last = executed[-1]
    health = ", ".join(f"{endpoint} {status}" for endpoint, status in last.health.items())
    return Outcome(
        incident_id=incident.incident_id,
        resolved=last.verified,
        actual_root_cause=suggestion.probable_root_cause,
        steps_that_worked=[f"{a.action}: {a.description}" for a in executed if a.verified],
        failed_attempts=[f"{a.action}: {a.description} (still failing: {_failing(a.health)})"
                         for a in executed if not a.verified],
        notes=f"Recorded automatically by the agent after verifying ShopFast. Health after last action: {health}.",
    )


def _attempt_line(attempt: RemediationAttempt) -> str:
    if not attempt.approved:
        result = "rejected, not run"
    elif not attempt.executed:
        result = f"could not run: {attempt.error}"
    else:
        result = "shop healthy afterwards" if attempt.verified else f"still failing: {_failing(attempt.health)}"
    return f"{attempt.at:%Y-%m-%d %H:%M:%S}Z {attempt.action} ({attempt.description}), chosen by {attempt.chosen_by}: {result}"


def postmortem_context(incident: Incident, suggestion: Suggestion, attempts: Sequence[RemediationAttempt]) -> str:
    """The incident record Hindsight reflect writes the postmortem from."""
    lines = [
        f"Incident {incident.incident_id} ({incident.severity.value}) in service {incident.service}: {incident.title}",
        f"Reported at: {incident.reported_at:%Y-%m-%d %H:%M:%S}Z",
        f"Symptoms: {incident.symptoms}",
        f"Error log: {incident.error_log}",
        f"Agent's diagnosis ({suggestion.confidence} confidence): {suggestion.probable_root_cause}",
        "Past incidents the agent used: " + (", ".join(s.incident_id for s in suggestion.similar_incidents) or "none"),
        "Action log:",
        *(f"- {_attempt_line(a)}" for a in attempts),
    ]
    if not attempts:
        lines.append("- no action was taken")
    return "\n".join(lines)


class IncidentService:
    def __init__(self, memory: IncidentMemory, advisor: IncidentAdvisor, shop: ShopFastClient | None = None) -> None:
        self._memory = memory
        self._advisor = advisor
        self._shop = shop

    def _actions(self) -> list[RemediationAction]:
        try:
            return self._shop.list_actions()
        except ShopFastError:
            return []

    def list_actions(self) -> list[RemediationAction]:
        """Allow-listed runbook actions, or [] when ShopFast is not configured or unreachable."""
        return self._actions() if self._shop is not None else []

    def detect_incident(self) -> Incident | None:
        """Probe ShopFast like a monitor. If an endpoint fails, open an incident from ShopFast's latest alert.

        Returns None when the shop is healthy, so an old alert is never imported after it was fixed.
        """
        if self._shop is None:
            raise ShopFastError("ShopFast is not configured")
        if _is_healthy(self._shop.health_check()):
            return None
        alert = self._shop.latest_incident()
        return incident_from_alert(alert) if alert else None

    def analyze_incident(self, incident: Incident, tried_actions: Sequence[str] = ()) -> Suggestion:
        """Recall similar past incidents, then ask the LLM for root cause, fix steps and one action to propose.

        Raises IncidentMemoryError when similar incidents cannot be recalled. Learned patterns are optional, and so
        are actions: without ShopFast the agent only advises. When the LLM fails, the recalled memory is still
        returned with llm_error set.
        """
        similar = self._memory.recall_similar(incident)
        try:
            patterns = self._memory.recall_learned_patterns(incident)
        except IncidentMemoryError:
            patterns = []
        if self._shop is None:
            try:
                return self._advisor.suggest(incident, similar, patterns)
            except LLMError as exc:
                return self._memory_only(similar, patterns, exc)
        actions = self._actions()
        try:
            suggestion = self._advisor.suggest(incident, similar, patterns, actions=actions,
                                               tried_actions=list(tried_actions))
        except LLMError as exc:
            suggestion = self._memory_only(similar, patterns, exc)
        evidence = action_evidence(suggestion.similar_incidents, [a.name for a in actions])
        return suggestion.model_copy(update={"action_evidence": evidence})

    @staticmethod
    def _memory_only(similar, patterns, exc: LLMError) -> Suggestion:
        return Suggestion(
            similar_incidents=similar,
            learned_patterns=patterns,
            probable_root_cause=LLM_UNAVAILABLE,
            confidence="low",
            memory_used=bool(similar),
            llm_error=str(exc),
        )

    def remediate(self, incident: Incident, suggestion: Suggestion, approved: bool,
                  previous_attempts: Sequence[RemediationAttempt] = (),
                  action: str | None = None) -> RemediationAttempt:
        """Act -> verify -> learn for the suggestion's proposed action, or for `action` chosen by the engineer.

        Runs nothing unless a human approved it. After running, probes ShopFast and records the outcome in memory
        automatically, including earlier failed attempts, so the next similar incident can recall it. An engineer's
        choice is recorded the same way, so the agent also learns from fixes it did not propose.
        """
        name = action or suggestion.proposed_action
        if self._shop is None or not name:
            raise ValueError("No proposed action to run")
        chosen = {a.name: a for a in self._shop.list_actions()}.get(name)
        if chosen is None:
            raise ValueError(f"Action {name!r} is not allow-listed")
        by_engineer = action is not None and action != suggestion.proposed_action
        attempt = dict(action=chosen.name, description=chosen.description,
                       reason=ENGINEER_CHOICE if by_engineer else suggestion.action_reason,
                       chosen_by="engineer" if by_engineer else "agent", approved=approved)
        if not approved:
            return RemediationAttempt(**attempt, executed=False, verified=False)
        try:
            self._shop.run_action(chosen.name)
            health = self._shop.health_check()
        except ShopFastError as exc:
            return RemediationAttempt(**attempt, executed=False, verified=False, error=str(exc))
        result = RemediationAttempt(**attempt, executed=True, verified=_is_healthy(health), health=health)
        try:
            self._memory.retain_outcome(incident, build_outcome(incident, suggestion, [*previous_attempts, result]))
        except IncidentMemoryError as exc:
            result.error = f"Action ran, but the outcome was not recorded in memory: {exc}"
        return result

    def record_outcome(self, incident: Incident, outcome: Outcome) -> None:
        """Manual fallback: retain an outcome the engineer typed in."""
        self._memory.retain_outcome(incident, outcome)

    def write_postmortem(self, incident: Incident, suggestion: Suggestion,
                         attempts: Sequence[RemediationAttempt]) -> Postmortem:
        """Hindsight reflect writes a blameless postmortem over this incident and the whole bank; the postmortem is
        then appended to the incident in memory, so its lessons come back whenever the incident is recalled.

        Raises IncidentMemoryError when reflect fails. If only storing fails, the postmortem is still returned with
        saved_to_memory False.
        """
        postmortem = self._memory.reflect_postmortem(incident, postmortem_context(incident, suggestion, attempts))
        try:
            self._memory.retain_postmortem(incident, postmortem)
        except IncidentMemoryError:
            return postmortem
        return postmortem.model_copy(update={"saved_to_memory": True})

    def runbook(self) -> str | None:
        """The living runbook Hindsight maintains (a mental model), or None when it does not exist yet."""
        return self._memory.get_runbook()

    def search_memory(self, query: str, limit: int = 10) -> list[str]:
        return self._memory.search(query, limit)
