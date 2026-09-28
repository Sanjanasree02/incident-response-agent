"""Builds the real service from settings, for entry points other than the Streamlit UI."""

from agent.actions import ShopFastClient
from agent.config import Settings, load_settings
from agent.desk import IncidentDesk
from agent.llm import IncidentAdvisor
from agent.memory import IncidentMemory
from agent.service import IncidentService


def build_desk(settings: Settings | None = None) -> IncidentDesk:
    settings = settings or load_settings()
    service = IncidentService(IncidentMemory(settings), IncidentAdvisor(settings), ShopFastClient.from_settings(settings))
    return IncidentDesk(service)
