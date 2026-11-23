"""The version check: parsing, the answer for each GitHub reply, and the route. No test reaches the network."""

import httpx
import pytest

from nexusvox.system import update_check
from nexusvox.system.update_check import RELEASES_URL, check_for_update, parse_version


def _reply(monkeypatch, status=200, body=None, exc=None):
    seen = {}

    def fake_get(url, headers=None, timeout=None, follow_redirects=False):
        seen.update(url=url, headers=headers, timeout=timeout, redirects=follow_redirects)
        if exc:
            raise exc
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(update_check.httpx, "get", fake_get)
    return seen


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v1.0.0", ((1,), 9, 0)),
        ("1.0", ((1,), 9, 0)),
        ("0.7.0", ((0, 7), 9, 0)),
        ("1.0.0rc1", ((1,), 3, 1)),
        ("v1.2.3-rc.2", ((1, 2, 3), 3, 2)),
        ("nightly", None),
        (None, None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_newer_release_links_to_its_page(monkeypatch):
    seen = _reply(monkeypatch, body={"tag_name": "v1.0.0", "html_url": f"{RELEASES_URL}/tag/v1.0.0"})
    result = check_for_update("0.7.0")
    assert result["ok"] and result["newer"] and result["message"] == "Version 1.0.0 is available."
    assert result["url"] == f"{RELEASES_URL}/tag/v1.0.0"
    assert seen["url"].startswith("https://api.github.com/") and seen["timeout"] <= 10 and seen["redirects"]
    assert set(seen["headers"]) == {"User-Agent", "Accept"}  # no identifier, no cookie, no token


def test_same_version_is_the_newest(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "v0.7.0", "html_url": f"{RELEASES_URL}/tag/v0.7.0"})
    result = check_for_update("0.7.0")
    assert result["ok"] and not result["newer"] and result["message"] == "You have the newest version."


def test_final_release_is_newer_than_its_release_candidate(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "v1.0.0"})
    assert check_for_update("1.0.0rc1")["newer"]
    _reply(monkeypatch, body={"tag_name": "v1.0.0-rc2"})
    result = check_for_update("1.0.0rc1")
    assert result["newer"] and result["message"] == "Version 1.0.0-rc2 is available."
    assert not check_for_update("1.0.0")["newer"]  # a candidate is older than the final


def test_a_hung_request_ends_at_the_deadline(monkeypatch):
    import threading

    release = threading.Event()
    monkeypatch.setattr(update_check, "DEADLINE_S", 0.05)
    monkeypatch.setattr(update_check.httpx, "get", lambda *a, **k: release.wait(5))
    try:
        assert check_for_update("0.7.0")["error"] == "offline"
    finally:
        release.set()


def test_a_non_http_exception_is_offline_not_a_crash(monkeypatch):
    _reply(monkeypatch, exc=RuntimeError("boom"))
    assert check_for_update("0.7.0")["error"] == "offline"


def test_installed_version_is_the_running_code():
    from nexusvox import __version__

    assert update_check.installed_version() == __version__


def test_one_point_zero_against_its_release_tag(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "v1.0.0"})
    assert not check_for_update("1.0.0")["newer"]  # equal
    assert check_for_update("0.7.0")["newer"]  # installed is older
    _reply(monkeypatch, body={"tag_name": "v0.7.0"})
    assert not check_for_update("1.0.0")["newer"]  # installed is newer
    _reply(monkeypatch, body={"tag_name": "v1.0.1"})
    assert check_for_update("1.0.0")["newer"]


def test_package_version_matches_pyproject():
    import tomllib
    from pathlib import Path

    from nexusvox import __version__

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"] == __version__ == "1.0.1"


def test_older_release_is_not_an_update(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "v0.6.0"})
    assert not check_for_update("0.7.0")["newer"]


def test_foreign_release_url_is_replaced(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "v2.0.0", "html_url": "https://evil.example/x"})
    assert check_for_update("0.7.0")["url"] == f"{RELEASES_URL}/tag/v2.0.0"


def test_offline_gives_a_plain_message(monkeypatch):
    _reply(monkeypatch, exc=httpx.ConnectError("no network"))
    result = check_for_update("0.7.0")
    assert result["error"] == "offline" and "internet" in result["message"]


def test_no_release_yet(monkeypatch):
    _reply(monkeypatch, status=404, body={})
    result = check_for_update("0.7.0")
    assert result["ok"] and not result["newer"] and result["latest"] is None
    assert "nothing to compare" in result["message"]  # never "up to date": a 404 can mean more than no release


@pytest.mark.parametrize("status", [403, 500])
def test_http_error(monkeypatch, status):
    _reply(monkeypatch, status=status, body={})
    assert check_for_update("0.7.0")["error"] == "server"


def test_unreadable_answer(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "nightly"})
    assert check_for_update("0.7.0")["error"] == "unreadable"


def _client(session_factory):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    return _create_app(DashboardAPI(session_factory, Config())).test_client()


def test_route_returns_the_check(monkeypatch, session_factory):
    _reply(monkeypatch, body={"tag_name": "v9.0.0"})
    response = _client(session_factory).post("/api/update-check")
    assert response.status_code == 200 and response.get_json()["newer"] is True


def test_route_reports_offline_as_an_error(monkeypatch, session_factory):
    _reply(monkeypatch, exc=httpx.ConnectError("no network"))
    response = _client(session_factory).post("/api/update-check")
    assert response.status_code == 502 and response.get_json()["error"] == "offline"


def test_settings_carry_the_installed_version(session_factory):
    assert _client(session_factory).get("/api/settings").get_json()["app_version"] == update_check.installed_version()


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/vzwrt13/NexusVox/releases", True),
        ("https://github.com/vzwrt13/NexusVox/releases/tag/v1.0.0", True),
        ("https://github.com/vzwrt13/NexusVox/releases?q=1", True),
        ("https://github.com/vzwrt13/NexusVox/releases-evil", False),
        ("https://github.com/vzwrt13/NexusVox/releases/../../x", False),
        ("https://github.com/vzwrt13/NexusVox", False),
        ("http://github.com/vzwrt13/NexusVox/releases", False),
        ("https://evil.example/https://github.com/vzwrt13/NexusVox/releases", False),
        ("https://github.com/vzwrt13/NexusVox/releases\nx", False),
        ("#", False),
        ("", False),
        (None, False),
    ],
)
def test_only_project_release_urls_are_allowed(url, allowed):
    assert update_check.is_release_url(url) is allowed


def test_open_route_hands_a_release_url_to_the_browser(monkeypatch, session_factory):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    client = _client(session_factory)
    good = "https://github.com/vzwrt13/NexusVox/releases/tag/v9.0.0"
    assert client.post("/api/update-check/open", json={"url": good}).status_code == 200
    for bad in ("#", "https://example.org/", None):
        assert client.post("/api/update-check/open", json={"url": bad}).status_code == 400
    assert opened == [good]
