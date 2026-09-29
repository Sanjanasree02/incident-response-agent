"""Streamlit Cloud secrets helpers: env loading and setup-mistake messages that never show values."""

import os

import pytest
import toml

from ui import secrets_check


def test_flatten_takes_top_level_and_section_keys():
    flat = secrets_check.flatten({"A": "1", "general": {"B": "2"}})
    assert flat == {"A": "1", "B": "2"}


def test_to_env_does_not_override_existing_variables(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "local")
    monkeypatch.setenv("FILL_ME", "")
    secrets_check.to_env({"KEEP_ME": "cloud", "section": {"FILL_ME": "cloud"}, "nested": {"skip": {"x": 1}}})
    assert os.environ["KEEP_ME"] == "local" and os.environ["FILL_ME"] == "cloud"


def test_seen_lists_names_only():
    assert secrets_check.seen({"GROQ_API_KEY": "gsk_secret", "s": {"UI_PASSWORD": "pw"}}) == "GROQ_API_KEY, UI_PASSWORD"
    assert secrets_check.seen({}) == "none"


def test_env_file_format_is_explained_without_values():
    """Lines pasted from .env (KEY=https://... without quotes) are invalid TOML."""
    with pytest.raises(toml.TomlDecodeError) as parse_error:
        toml.loads("GROQ_API_KEY=gsk_do_not_show\nHINDSIGHT_BASE_URL=https://x.test\n")
    # Streamlit wraps the parser error like this (streamlit/runtime/secrets.py)
    message = secrets_check.problem(Exception(f"Error parsing secrets file at /app/secrets.toml: {parse_error.value}"))
    assert "not valid TOML" in message and "line 1 column 1" in message
    assert "gsk_do_not_show" not in message and "x.test" not in message


def test_missing_secrets_file_is_just_none():
    assert secrets_check.problem(Exception("No secrets found. Valid paths ...")) == "none"
