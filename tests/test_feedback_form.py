"""The Send feedback link: URL building and the hidden state."""

from urllib.parse import parse_qs, urlsplit

from nexusvox.config import Config, SupportConfig, load_config, save_config
from nexusvox.system.feedback_form import build_feedback_url, hardware_tier, windows_version

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


def test_defaults_come_from_the_running_app():
    query = _query(build_feedback_url("https://example.org/f"))
    assert query["app_version"][0]
    assert query["windows_version"][0]
    assert query["hardware"][0] in ("NVIDIA GPU", "CPU-only")


def test_hardware_tier_follows_the_device_in_use(monkeypatch):
    monkeypatch.setattr("nexusvox.system.feedback_form._gpu_present", lambda: True)
    assert hardware_tier("auto") == "NVIDIA GPU"
    assert hardware_tier("cuda") == "NVIDIA GPU"
    assert hardware_tier("cpu") == "CPU-only"
    monkeypatch.setattr("nexusvox.system.feedback_form._gpu_present", lambda: False)
    assert hardware_tier("auto") == "CPU-only"


def test_windows_11_is_told_apart_by_build(monkeypatch):
    class Info:
        major, minor, build = 10, 0, 26200

    windows_version.cache_clear()
    monkeypatch.setattr("sys.getwindowsversion", lambda: Info, raising=False)
    try:
        assert windows_version() == "Windows 11 (10.0.26200)"
        windows_version.cache_clear()
        Info.build = 19045
        assert windows_version() == "Windows 10 (10.0.19045)"
    finally:
        windows_version.cache_clear()


def test_settings_expose_the_url(db, session_factory):
    from nexusvox.dashboard.api import DashboardAPI

    config = Config(support=SupportConfig(feedback_form_url="https://example.org/f"))
    url = DashboardAPI(session_factory, config, db=db).get_settings()["feedback_url"]
    assert url.startswith("https://example.org/f?") and "app_version=" in url


def test_settings_hide_the_button_by_default(flask_client):
    assert flask_client.get("/api/settings").get_json()["feedback_url"] == ""


def test_config_defaults_to_no_form_and_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    assert load_config(path).support.feedback_form_url == ""
    save_config(Config(support=SupportConfig(feedback_form_url="https://example.org/f?x=1")), path)
    assert load_config(path).support.feedback_form_url == "https://example.org/f?x=1"
    save_config(Config(), path)
    assert load_config(path).support.feedback_form_url == ""


def test_malformed_form_url_is_logged_and_unset(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text('[support]\nfeedback_form_url = "nexusvox.example/feedback"\n', encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert load_config(path).support.feedback_form_url == ""
    assert "feedback_form_url" in caplog.text


def test_feedback_tab_opens_the_log_folder_through_the_existing_endpoint(flask_client, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("nexusvox.dashboard.api_settings.log_dir", lambda: tmp_path)
    monkeypatch.setattr("os.startfile", opened.append, raising=False)
    assert flask_client.post("/api/log-folder/open").get_json()["ok"] is True
    assert opened == [tmp_path]
    assert flask_client.get("/api/settings").get_json()["log_dir"] == str(tmp_path)
