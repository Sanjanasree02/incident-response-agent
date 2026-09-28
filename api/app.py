"""REST API: lets any project use the incident agent. Run: uvicorn api.app:app --host 127.0.0.1 --port 8002

Every endpoint except /health needs the header X-API-Key matching AGENT_API_KEY from .env; without that variable the
API refuses all calls. Calling POST /incidents/{id}/actions is the human approval: the API runs only the allow-listed
action named (or proposed), then verifies ShopFast and records the outcome in memory.
"""

import os
import secrets
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from agent.actions import ShopFastError
from agent.desk import IncidentDesk, OpenIncident, UnknownIncident
from agent.memory import IncidentMemoryError
from agent.models import (MAX_TEXT_CHARS, Incident, Postmortem, RemediationAttempt, Severity, Step, Suggestion,
                          TeamRule)

app = FastAPI(title="Incident Response Agent API", version="1.0")


@lru_cache(maxsize=1)
def get_desk() -> IncidentDesk:
    from agent.factory import build_desk

    return build_desk()


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("AGENT_API_KEY", "").strip()
    if not expected or expected == "replace-me":
        raise HTTPException(status_code=503, detail="AGENT_API_KEY is not configured on the server")
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


Desk = Annotated[IncidentDesk, Depends(get_desk)]
authorized = [Depends(require_api_key)]


class NewIncident(BaseModel):
    service: str
    severity: Severity
    title: str
    symptoms: str
    error_log: str = ""


class IncidentView(BaseModel):
    incident: Incident
    suggestion: Suggestion
    attempts: list[RemediationAttempt]
    resolved: bool


class ActionRequest(BaseModel):
    action: str | None = Field(default=None, description="Allow-listed action; omit to run the agent's proposal")


class OutcomeRequest(BaseModel):
    resolved: bool
    actual_root_cause: str = Field(min_length=3, max_length=MAX_TEXT_CHARS)
    steps_that_worked: list[Step] = Field(default_factory=list, max_length=20)
    failed_attempts: list[Step] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=MAX_TEXT_CHARS)


def view(item: OpenIncident) -> IncidentView:
    return IncidentView(incident=item.incident, suggestion=item.suggestion, attempts=item.attempts,
                        resolved=item.resolved)


@app.exception_handler(UnknownIncident)
def unknown_incident(request: Request, exc: UnknownIncident) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"Unknown incident {exc.args[0]}; analyze it first"})


@app.exception_handler(IncidentMemoryError)
@app.exception_handler(ShopFastError)
def upstream_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
def invalid(request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors(include_url=False, include_context=False)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/incidents/detect", dependencies=authorized)
def detect(desk: Desk) -> IncidentView | None:
    """Probe ShopFast; if something fails, open and analyze an incident. null when the shop is healthy."""
    item = desk.detect()
    return view(item) if item else None


@app.post("/incidents/analyze", dependencies=authorized)
def analyze(body: NewIncident, desk: Desk) -> IncidentView:
    return view(desk.analyze(Incident(**body.model_dump())))


@app.get("/incidents/{incident_id}", dependencies=authorized)
def get_incident(incident_id: str, desk: Desk) -> IncidentView:
    return view(desk.get(incident_id))


@app.post("/incidents/{incident_id}/reanalyze", dependencies=authorized)
def reanalyze(incident_id: str, desk: Desk) -> IncidentView:
    return view(desk.reanalyze(incident_id))


@app.post("/incidents/{incident_id}/actions", dependencies=authorized)
def act(incident_id: str, body: ActionRequest, desk: Desk) -> RemediationAttempt:
    try:
        return desk.act(incident_id, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/incidents/{incident_id}/outcome", status_code=204, dependencies=authorized)
def record_outcome(incident_id: str, body: OutcomeRequest, desk: Desk) -> None:
    desk.record_outcome(incident_id, **body.model_dump())


@app.post("/incidents/{incident_id}/postmortem", dependencies=authorized)
def postmortem(incident_id: str, desk: Desk) -> Postmortem:
    return desk.postmortem(incident_id)


@app.get("/runbook", dependencies=authorized)
def runbook(desk: Desk) -> dict:
    return {"runbook": desk.runbook()}


@app.get("/rules", dependencies=authorized)
def list_rules(desk: Desk) -> list[TeamRule]:
    return desk.team_rules()


@app.post("/rules", status_code=201, dependencies=authorized)
def add_rule(rule: TeamRule, desk: Desk) -> TeamRule:
    desk.add_team_rule(rule)
    return rule


@app.get("/memory/search", dependencies=authorized)
def search(desk: Desk, q: Annotated[str, Query(min_length=3, max_length=500)],
           limit: Annotated[int, Query(ge=1, le=50)] = 10) -> dict:
    return {"results": desk.search(q, limit)}
