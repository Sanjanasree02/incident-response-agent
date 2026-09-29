"""Streamlit Community Cloud secrets: load them into the environment and explain setup mistakes.

Messages name secrets and TOML line numbers only, never secret values.
"""

import os
import re
from collections.abc import Mapping


def flatten(items: Mapping) -> dict[str, object]:
    """Top-level keys, plus keys under a [section] header (Streamlit itself only promotes top-level keys)."""
    flat: dict[str, object] = {}
    for key, value in items.items():
        flat.update(value if isinstance(value, Mapping) else {key: value})
    return flat


def to_env(items: Mapping) -> None:
    """Copy text and number secrets into os.environ, without overriding variables that are already set."""
    for name, value in flatten(items).items():
        if isinstance(value, (str, int, float)) and not os.environ.get(name):
            os.environ[name] = str(value)


def problem(exc: Exception) -> str:
    """What went wrong reading st.secrets, for the configuration error."""
    text = str(exc)
    if "Error parsing secrets file" in text:
        where = ", ".join(re.findall(r"line \d+ column \d+", text)) or "unknown line"
        return (f"none, because the Secrets box is not valid TOML ({where}). Put every value in double quotes, "
                'for example HINDSIGHT_BASE_URL = "https://..." (the .env format KEY=value does not work there)')
    return "none"


def seen(items: Mapping) -> str:
    return ", ".join(sorted(flatten(items))) or "none"
