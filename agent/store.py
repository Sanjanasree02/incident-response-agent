"""SQLite store for open incidents: the incident, the agent's latest suggestion, the action log and the postmortem.

Hindsight holds what the agent learned; this store holds work in progress, so a UI refresh or an API/MCP restart does
not lose an incident someone is still working on. One row per incident, JSON columns validated by the models on read.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from agent.models import Incident, Postmortem, RemediationAttempt, Suggestion

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "incidents.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    incident TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    attempts TEXT NOT NULL,
    postmortem TEXT
)
"""


@dataclass
class OpenIncident:
    incident: Incident
    suggestion: Suggestion
    attempts: list[RemediationAttempt] = field(default_factory=list)
    postmortem: Postmortem | None = None

    @property
    def resolved(self) -> bool:
        return any(a.verified for a in self.attempts)


class IncidentStore:
    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by threads (Streamlit, FastAPI); the lock serializes access.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = Lock()
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)

    def save(self, item: OpenIncident) -> None:
        row = (
            item.incident.incident_id,
            datetime.now(timezone.utc).isoformat(),
            item.incident.model_dump_json(),
            item.suggestion.model_dump_json(),
            "[" + ",".join(a.model_dump_json() for a in item.attempts) + "]",
            item.postmortem.model_dump_json() if item.postmortem else None,
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(incident_id) DO UPDATE SET "
                "updated_at=excluded.updated_at, incident=excluded.incident, suggestion=excluded.suggestion, "
                "attempts=excluded.attempts, postmortem=excluded.postmortem",
                row,
            )

    def get(self, incident_id: str) -> OpenIncident | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT incident, suggestion, attempts, postmortem FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return _load(row) if row else None

    def recent(self, limit: int = 20) -> list[OpenIncident]:
        """Most recently updated first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT incident, suggestion, attempts, postmortem FROM incidents "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_load(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _load(row: tuple) -> OpenIncident:
    incident, suggestion, attempts, postmortem = row
    return OpenIncident(
        incident=Incident.model_validate_json(incident),
        suggestion=Suggestion.model_validate_json(suggestion),
        attempts=[RemediationAttempt.model_validate(a) for a in json.loads(attempts)],
        postmortem=Postmortem.model_validate_json(postmortem) if postmortem else None,
    )
