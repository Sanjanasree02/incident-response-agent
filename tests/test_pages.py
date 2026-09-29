"""The GitHub Pages wrapper frames the Streamlit app in embed mode."""

from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
APP_URL = "https://incident-response-agent-ui.streamlit.app/"


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags():
    parser = _Tags()
    parser.feed((SITE / "index.html").read_text(encoding="utf-8"))
    return parser.tags


def test_wrapper_frames_the_app_in_embed_mode():
    frames = [attrs for tag, attrs in _tags() if tag == "iframe"]
    assert len(frames) == 1
    assert frames[0]["src"] == APP_URL + "?embed=true"  # no Cloud profile badge or toolbar


def test_wrapper_links_to_the_app_when_frames_are_blocked():
    links = [attrs.get("href") for tag, attrs in _tags() if tag == "a"]
    assert APP_URL in links


def test_wrapper_icon_is_shipped_with_the_site():
    icons = [attrs["href"] for tag, attrs in _tags() if tag == "link" and attrs.get("rel") == "icon"]
    assert icons and all((SITE / href).is_file() for href in icons)
