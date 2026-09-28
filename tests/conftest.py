"""Every test gets its own SQLite file for open incidents, so the UI never reads or writes data/incidents.db."""

import pytest


@pytest.fixture(autouse=True)
def isolated_incident_db(tmp_path, monkeypatch):
    monkeypatch.setenv("INCIDENT_DB", str(tmp_path / "incidents.db"))
