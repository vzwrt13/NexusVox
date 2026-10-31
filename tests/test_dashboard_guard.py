"""Access checks of the dashboard server: Host, Origin and the session cookie."""

from __future__ import annotations

import time

import pytest

from nexusvox.config import Config
from nexusvox.dashboard import _PORT, _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.dashboard.guard import COOKIE_NAME, LAUNCH_PARAM, LAUNCH_TTL_S

HOST = f"127.0.0.1:{_PORT}"


@pytest.fixture
def app(db, session_factory):
    return _create_app(DashboardAPI(session_factory, Config(), db=db))


@pytest.fixture
def guard(app):
    return app.extensions["nexusvox_guard"]


class _Client:
    """A client with its own host and cookie; it does not use the signed-in helper from conftest."""

    def __init__(self, app, cookie: str | None = None, host: str = HOST) -> None:
        self._client = app.test_client()
        self._client.delete_cookie(COOKIE_NAME, domain="127.0.0.1")
        self.host = host
        if cookie is not None:
            self._client.set_cookie(COOKIE_NAME, cookie, domain=host.split(":")[0])

    def _call(self, method, path, **kwargs):
        return getattr(self._client, method)(path, base_url=f"http://{self.host}", **kwargs)

    def get(self, path, **kwargs):
        return self._call("get", path, **kwargs)

    def post(self, path, **kwargs):
        return self._call("post", path, **kwargs)


def _client(app, cookie: str | None = None, host: str = HOST) -> _Client:
    return _Client(app, cookie, host)


def test_valid_request_passes(app, guard):
    assert _client(app, guard.session_token).get("/api/snippets").status_code == 200


def test_missing_token_is_rejected(app):
    client = _client(app)
    assert client.get("/api/snippets").status_code == 403
    assert client.get("/api/snippets/github-repos").status_code == 403
    assert client.post("/api/models/switch", json={"model": "x"}).status_code == 403
    assert client.get("/").status_code == 403


def test_wrong_token_is_rejected(app):
    assert _client(app, "not-the-token").get("/api/snippets").status_code == 403


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:47392", "127.0.0.1", "127.0.0.1:1234"])
def test_foreign_host_is_rejected_even_with_token(app, guard, host):
    client = _client(app, guard.session_token, host)
    assert client.get("/api/snippets").status_code == 403


def test_localhost_host_is_accepted(app, guard):
    client = _client(app, guard.session_token, f"localhost:{_PORT}")
    assert client.get("/api/snippets").status_code == 200


def test_cross_origin_is_rejected_even_with_token(app, guard):
    client = _client(app, guard.session_token)
    assert client.get("/api/snippets", headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert client.post("/api/models/switch", json={}, headers={"Origin": "null"}).status_code == 403


def test_same_origin_is_accepted(app, guard):
    client = _client(app, guard.session_token)
    assert client.get("/api/snippets", headers={"Origin": f"http://{HOST}"}).status_code == 200


def test_launch_secret_is_traded_for_the_cookie_once(app, guard):
    secret = guard.new_launch_secret()
    client = _client(app)
    first = client.get(f"/?{LAUNCH_PARAM}={secret}&tab=meetings")
    assert first.status_code == 302
    assert first.headers["Location"].endswith("/#meetings")
    cookie = first.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert client._client.get("/api/snippets", base_url=f"http://{HOST}").status_code == 200  # cookie jar holds it

    assert _client(app).get(f"/?{LAUNCH_PARAM}={secret}").status_code == 403  # spent


def test_unknown_launch_secret_is_rejected(app):
    assert _client(app).get(f"/?{LAUNCH_PARAM}=guess").status_code == 403


def test_cross_port_same_site_request_is_rejected(app, guard):
    """A page on another localhost port is same-site: the cookie rides along, but it is not same-origin."""
    client = _client(app, guard.session_token)
    assert client.get("/api/snippets", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/api/snippets", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/snippets", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.get("/", headers={"Sec-Fetch-Site": "none"}).status_code == 200  # typed or bookmarked


def test_api_refusal_is_json_the_script_recognises(app):
    resp = _client(app).get("/api/extensions")
    assert resp.status_code == 403
    assert resp.headers["X-NexusVox-Denied"] == "1"
    assert resp.get_json()["denied"] is True
    page = _client(app).get("/")
    assert page.headers["X-NexusVox-Denied"] == "1" and page.mimetype == "text/html"


def test_launch_secret_expires(app, guard, monkeypatch):
    secret = guard.new_launch_secret()
    clock = time.monotonic()
    monkeypatch.setattr("nexusvox.dashboard.guard.time.monotonic", lambda: clock + LAUNCH_TTL_S + 1)
    assert _client(app).get(f"/?{LAUNCH_PARAM}={secret}").status_code == 403
    guard.new_launch_secret()
    assert len(guard._launch_secrets) == 1  # the expired one is dropped when a new one is minted


@pytest.mark.parametrize("tab", ["meetings", "a-b-1"])
def test_good_tab_goes_into_the_redirect(app, guard, tab):
    resp = _client(app).get(f"/?{LAUNCH_PARAM}={guard.new_launch_secret()}&tab={tab}")
    assert resp.headers["Location"].endswith(f"/#{tab}")


@pytest.mark.parametrize("tab", ["%0D%0Aevil", "A%20B", "x" * 40, "a/b"])
def test_odd_tab_is_dropped_and_the_secret_still_works(app, guard, tab):
    resp = _client(app).get(f"/?{LAUNCH_PARAM}={guard.new_launch_secret()}&tab={tab}")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/") and "#" not in resp.headers["Location"]


def test_extension_routes_are_covered(db, session_factory, tmp_path):
    """The routes an extension mounts (backend blueprint and panel files) sit behind the same hook."""
    from test_extensions import DummyExtension

    from nexusvox.extensions import ExtensionRegistry

    static = tmp_path / "panel-static"
    static.mkdir()
    (static / "panel.js").write_text("// panel", encoding="utf-8")
    registry = ExtensionRegistry()
    assert registry.add(DummyExtension(static))
    app = _create_app(DashboardAPI(session_factory, Config(), db=db, extensions=registry))
    token = app.extensions["nexusvox_guard"].session_token

    for url in ("/api/ext/dummy-tab/hello", "/ext/dummy-tab/panel.js"):
        assert _client(app).get(url).status_code == 403, url
        assert _client(app, "wrong").get(url).status_code == 403, url
        assert _client(app, token).get(url).status_code == 200, url
        assert _client(app, token, "evil.example.com").get(url).status_code == 403, url


def test_every_route_without_arguments_is_refused_without_the_cookie(app):
    client = _client(app)
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and not rule.arguments:
            assert client.get(rule.rule).status_code == 403, rule.rule


def test_a_busy_port_means_no_browser_and_no_started_flag(db, monkeypatch):
    import nexusvox.dashboard as dashboard

    def busy(*args, **kwargs):
        raise OSError("address in use")

    opened: list[str] = []
    monkeypatch.setattr(dashboard, "make_server", busy)
    monkeypatch.setattr(dashboard.webbrowser, "open", opened.append)
    monkeypatch.setattr(dashboard, "_server_started", False)
    monkeypatch.setattr(dashboard, "_guard", None)
    monkeypatch.setattr(dashboard.DashboardAPI, "sweep_meeting_leftovers", lambda self: None)

    dashboard.open_dashboard(db, Config())

    assert opened == []
    assert dashboard._server_started is False
