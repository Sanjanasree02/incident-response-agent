"""MCP server: gives other AI agents (Claude Code, Cursor, any MCP client) the incident agent and its Hindsight memory.

Run over stdio: python -m mcp_server.server   (see README for the client config)

Tools that change the shop or memory are marked destructive, so MCP clients ask the human before calling them;
that confirmation is the approval gate. Actions stay limited to ShopFast's allow-list.
"""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from agent.actions import ShopFastError
from agent.desk import IncidentDesk, OpenIncident, UnknownIncident
from agent.memory import IncidentMemoryError
from agent.models import Incident

INSTRUCTIONS = """Incident response agent for ShopFast with long-term memory (Hindsight).
Typical flow: detect_incident (or analyze_incident), then run_action with the proposed action after the user
approves, then write_postmortem. Outcomes of actions are recorded in memory automatically; use record_outcome only
for fixes done outside run_action. get_runbook shows what memory has learned; search_memory answers free questions."""

server = MCPServer(name="incident-memory", instructions=INSTRUCTIONS)
_desk: IncidentDesk | None = None
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def desk() -> IncidentDesk:
    global _desk
    if _desk is None:
        from agent.factory import build_desk

        _desk = build_desk()
    return _desk


def set_desk(value: IncidentDesk | None) -> None:
    """For tests: use a desk with fakes."""
    global _desk
    _desk = value


def _view(item: OpenIncident) -> dict:
    s = item.suggestion
    return {
        "incident": item.incident.model_dump(mode="json"),
        "memory_used": s.memory_used,
        "similar_incidents": [{"incident_id": i.incident_id, "summary": i.summary} for i in s.similar_incidents],
        "probable_root_cause": s.probable_root_cause,
        "fix_steps": s.fix_steps,
        "avoid_steps": s.avoid_steps,
        "confidence": s.confidence,
        "proposed_action": s.proposed_action,
        "action_reason": s.action_reason,
        "evidence": [e.model_dump() for e in s.action_evidence],
        "llm_error": s.llm_error,
        "attempts": [a.model_dump(mode="json") for a in item.attempts],
        "resolved": item.resolved,
    }


def _error(exc: Exception) -> dict:
    if isinstance(exc, UnknownIncident):
        return {"error": f"Unknown incident {exc.args[0]}; call analyze_incident or detect_incident first"}
    return {"error": str(exc)}


EXPECTED = (UnknownIncident, IncidentMemoryError, ShopFastError, ValidationError, ValueError)


@server.tool(annotations=READ)
def detect_incident() -> dict:
    """Probe ShopFast's endpoints. If one fails, open an incident from the latest alert and analyze it with memory."""
    try:
        item = desk().detect()
    except EXPECTED as exc:
        return _error(exc)
    return _view(item) if item else {"healthy": True, "message": "No failing ShopFast endpoint."}


@server.tool(annotations=READ)
def analyze_incident(service: str, severity: str, title: str, symptoms: str, error_log: str = "") -> dict:
    """Recall similar past incidents from memory and suggest root cause, fix steps, steps to avoid and one action.

    severity is SEV1, SEV2 or SEV3. service is lowercase, e.g. payment-api.
    """
    try:
        incident = Incident(service=service, severity=severity, title=title, symptoms=symptoms, error_log=error_log)
        return _view(desk().analyze(incident))
    except EXPECTED as exc:
        return _error(exc)


@server.tool(annotations=WRITE)
def run_action(incident_id: str, action: str = "") -> dict:
    """Run an allow-listed ShopFast runbook action for an open incident, verify the shop, record the outcome.

    Leave action empty to run the agent's proposal. Only call after the user approved this action.
    """
    try:
        attempt = desk().act(incident_id, action or None)
    except EXPECTED as exc:
        return _error(exc)
    return attempt.model_dump(mode="json")


@server.tool(annotations=READ)
def next_suggestion(incident_id: str) -> dict:
    """Re-analyze an open incident without the actions already tried."""
    try:
        return _view(desk().reanalyze(incident_id))
    except EXPECTED as exc:
        return _error(exc)


@server.tool(annotations=WRITE)
def record_outcome(incident_id: str, resolved: bool, actual_root_cause: str,
                   steps_that_worked: list[str] | None = None, failed_attempts: list[str] | None = None,
                   notes: str = "") -> dict:
    """Store what fixed (or did not fix) an open incident in memory, for fixes done outside run_action."""
    try:
        outcome = desk().record_outcome(incident_id, resolved, actual_root_cause, steps_that_worked or [],
                                        failed_attempts or [], notes)
    except EXPECTED as exc:
        return _error(exc)
    return {"recorded": True, "outcome": outcome.model_dump()}


@server.tool(annotations=WRITE)
def write_postmortem(incident_id: str) -> dict:
    """Write a blameless postmortem with Hindsight reflect over this incident and all past ones; store it in memory."""
    try:
        return desk().postmortem(incident_id).model_dump()
    except EXPECTED as exc:
        return _error(exc)


@server.tool(annotations=READ)
def get_runbook() -> dict:
    """The living runbook: which fixes worked and failed per failure type, maintained by Hindsight."""
    try:
        return {"runbook": desk().runbook()}
    except EXPECTED as exc:
        return _error(exc)


@server.tool(annotations=READ)
def search_memory(query: str, limit: int = 10) -> dict:
    """Search incident memory with a free-text question, e.g. 'what fixed Redis timeouts on cart-service?'."""
    try:
        return {"results": desk().search(query, max(1, min(limit, 50)))}
    except EXPECTED as exc:
        return _error(exc)


if __name__ == "__main__":
    server.run("stdio")
