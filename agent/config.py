"""Loads configuration from environment variables. Fails fast when a required value is missing."""

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

_BANK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    hindsight_base_url: str
    hindsight_api_key: str
    hindsight_bank_id: str
    groq_api_key: str
    groq_model: str
    shopfast_url: str = "http://127.0.0.1:8001"
    incident_db: str = ""  # SQLite file for open incidents; empty = data/incidents.db


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value == "replace-me":
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def validate_bank_id(bank_id: str) -> str:
    if not _BANK_ID_RE.fullmatch(bank_id):
        raise ConfigError(f"Invalid bank ID {bank_id!r}: use 3-64 lowercase letters, digits or hyphens")
    return bank_id


def load_settings(bank_id_override: str | None = None) -> Settings:
    """Read settings from the environment (and a local .env file, if present).

    bank_id_override lets scripts target a fresh demo bank, e.g. shopfast-incidents-demo3.
    """
    load_dotenv()
    bank_id = bank_id_override or os.getenv("HINDSIGHT_BANK_ID", "shopfast-incidents")
    return Settings(
        hindsight_base_url=_require("HINDSIGHT_BASE_URL"),
        hindsight_api_key=_require("HINDSIGHT_API_KEY"),
        hindsight_bank_id=validate_bank_id(bank_id),
        groq_api_key=_require("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b",
        shopfast_url=f"http://{os.getenv('SHOPFAST_HOST', '127.0.0.1')}:{os.getenv('SHOPFAST_PORT', '8001')}",
        incident_db=os.getenv("INCIDENT_DB", "").strip(),
    )
