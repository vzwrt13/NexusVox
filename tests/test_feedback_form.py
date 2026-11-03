"""The Send feedback link: URL building and the hidden state."""

from urllib.parse import parse_qs, urlsplit

from nexusvox.config import Config, SupportConfig
from nexusvox.feedback_form import build_feedback_url

FACTS = {"app_version": "1.2.3", "windows": "Windows 11 (10.0.26200)", "hardware": "CPU-only"}


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def test_adds_exactly_the_three_facts():
    url = build_feedback_url("https://example.org/feedback", **FACTS)
    assert url.startswith("https://example.org/feedback?")
    assert _query(url) == {
        "app_version": ["1.2.3"],
        "windows_version": ["Windows 11 (10.0.26200)"],
        "hardware": ["CPU-only"],
    }


def test_keeps_existing_parameters():
    url = build_feedback_url("https://example.org/f?form=7", **FACTS)
    assert _query(url)["form"] == ["7"]
    assert len(_query(url)) == 4


def test_empty_url_hides_the_button():
    assert build_feedback_url("", **FACTS) == ""
    assert build_feedback_url("   ", **FACTS) == ""


def test_only_http_urls_are_opened():
    assert build_feedback_url("file:///C:/secret", **FACTS) == ""
    assert build_feedback_url("javascript:alert(1)", **FACTS) == ""


def test_defaults_come_from_the_running_app(monkeypatch):
    monkeypatch.setattr("nexusvox.feedback_form.cuda_available", lambda: True)
    query = _query(build_feedback_url("https://example.org/f"))
    assert query["hardware"] == ["NVIDIA GPU"]
    assert query["app_version"][0]


def test_settings_expose_the_url_or_nothing(flask_client):
    settings = flask_client.get("/api/settings").get_json()
    assert settings["feedback_url"].startswith("https://")
    assert "app_version=" in settings["feedback_url"]


def test_settings_hide_the_button_when_unconfigured(db, session_factory):
    from nexusvox.dashboard.api import DashboardAPI

    config = Config(support=SupportConfig(feedback_form_url=""))
    assert DashboardAPI(session_factory, config, db=db).get_settings()["feedback_url"] == ""


def test_default_config_has_a_form_url():
    assert Config().support.feedback_form_url.startswith("https://")
    assert SupportConfig(feedback_form_url="").feedback_form_url == ""
