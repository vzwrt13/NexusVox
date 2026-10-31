"""Access checks of the dashboard server: Host, Origin and the session cookie."""

from __future__ import annotations

import pytest

from nexusvox.config import Config
from nexusvox.dashboard import _PORT, _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.dashboard.guard import COOKIE_NAME, LAUNCH_PARAM

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


def test_extension_routes_are_covered(app):
    """Every registered route sits behind the same hook, extension blueprints included."""
    client = _client(app)
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and not rule.arguments:
            assert client.get(rule.rule).status_code == 403, rule.rule
