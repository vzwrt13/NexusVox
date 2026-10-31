"""Access control for the dashboard server: Host, Origin and a per-session cookie.

The server binds to 127.0.0.1, but that alone does not stop other local programs or web pages
(DNS rebinding included) from calling the API. Three checks run before every request, the routes
that extensions mount included:

* **Host** must be ``127.0.0.1`` or ``localhost`` with the dashboard port. A rebinding page reaches the
  server under its own hostname, so it is refused here.
* **Origin**, where a browser sends it, must be one of those same origins.
* **Session cookie**: an ``HttpOnly``, ``SameSite=Strict`` cookie carrying a random per-run token. The
  browser gets it by opening the one-time launch URL (``/?launch=<secret>``) that ``open_dashboard``
  builds in memory; the secret is spent on first use. A program that merely requests ``/`` gets nothing.

After a restart of NexusVox the token changes; an old dashboard tab is refused until it is reopened
from the tray.
"""

from __future__ import annotations

import hmac
import secrets
import threading

from flask import Flask, Response, redirect, request

COOKIE_NAME = "nexusvox_session"
LAUNCH_PARAM = "launch"

_DENIED_PAGE = (
    "<!doctype html><title>NexusVox</title><body style='font-family:sans-serif;margin:3em'>"
    "<h1>Access denied</h1><p>Open the dashboard from the NexusVox tray icon.</p></body>"
)


class DashboardGuard:
    """Holds the session token and the one-time launch secrets for one dashboard server."""

    def __init__(self, port: int) -> None:
        self._port = port
        self._session_token = secrets.token_urlsafe(32)
        self._launch_secrets: set[str] = set()
        self._lock = threading.Lock()

    @property
    def session_token(self) -> str:
        return self._session_token

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self._port}", f"localhost:{self._port}"}

    def allowed_origins(self) -> set[str]:
        return {f"http://{host}" for host in self.allowed_hosts()}

    def new_launch_secret(self) -> str:
        """A single-use secret that the browser trades for the session cookie."""
        secret = secrets.token_urlsafe(24)
        with self._lock:
            self._launch_secrets.add(secret)
        return secret

    def _spend(self, secret: str) -> bool:
        with self._lock:
            if secret in self._launch_secrets:
                self._launch_secrets.discard(secret)
                return True
        return False

    def _has_token(self) -> bool:
        cookie = request.cookies.get(COOKIE_NAME, "")
        return bool(cookie) and hmac.compare_digest(cookie, self._session_token)

    def check(self) -> Response | tuple[str, int] | None:
        """The before_request hook: None lets the request through."""
        if request.host.lower() not in self.allowed_hosts():
            return _deny()
        origin = request.headers.get("Origin")
        if origin is not None and origin.lower() not in self.allowed_origins():
            return _deny()

        launch = request.args.get(LAUNCH_PARAM)
        if launch is not None and request.method == "GET" and request.path == "/":
            if not self._spend(launch):
                return _deny()
            target = "/" + (f"#{request.args['tab']}" if request.args.get("tab") else "")
            response = redirect(target)
            response.set_cookie(COOKIE_NAME, self._session_token, httponly=True, samesite="Strict", path="/")
            return response

        if not self._has_token():
            return _deny()
        return None


def _deny() -> tuple[str, int]:
    return _DENIED_PAGE, 403


def install_guard(app: Flask, port: int) -> DashboardGuard:
    guard = DashboardGuard(port)
    app.before_request(guard.check)
    app.extensions["nexusvox_guard"] = guard
    return guard
