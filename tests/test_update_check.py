"""The version check: parsing, the answer for each GitHub reply, and the route. No test reaches the network."""

import httpx
import pytest

from nexusvox.system import update_check
from nexusvox.system.update_check import RELEASES_URL, check_for_update, parse_version


def _reply(monkeypatch, status=200, body=None, exc=None):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        if exc:
            raise exc
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(update_check.httpx, "get", fake_get)
    return seen


@pytest.mark.parametrize(
    ("text", "expected"),
    [("v1.0.0", (1,)), ("1.0", (1,)), ("0.7.0", (0, 7)), ("v1.2.3-rc1", (1, 2, 3)), ("nightly", None), (None, None)],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_newer_release_links_to_its_page(monkeypatch):
    seen = _reply(monkeypatch, body={"tag_name": "v1.0.0", "html_url": f"{RELEASES_URL}/tag/v1.0.0"})
    result = check_for_update("0.7.0")
    assert result["ok"] and result["newer"] and result["message"] == "Version 1.0.0 is available."
    assert result["url"] == f"{RELEASES_URL}/tag/v1.0.0"
    assert seen["url"].startswith("https://api.github.com/") and seen["timeout"] <= 10
    assert set(seen["headers"]) == {"User-Agent", "Accept"}  # no identifier, no cookie, no token


def test_same_version_is_the_newest(monkeypatch):
    _reply(monkeypatch, body={"tag_name": "v0.7.0", "html_url": f"{RELEASES_URL}/tag/v0.7.0"})
    result = check_for_update("0.7.0")
    assert result["ok"] and not result["newer"] and result["message"] == "You have the newest version."


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
    assert result["ok"] and not result["newer"] and "No release" in result["message"]


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
