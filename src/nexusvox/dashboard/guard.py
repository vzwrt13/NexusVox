"""Access control for the dashboard server: Host, Origin and a per-session cookie.

The server binds to 127.0.0.1, but that alone does not stop other local programs or web pages
(DNS rebinding included) from calling the API. Three checks run before every request, the routes
that extensions mount included:

* **Host** must be ``127.0.0.1`` or ``localhost`` with the dashboard port. A rebinding page reaches the
  server under its own hostname, so it is refused here.
* **Origin**, where a browser sends it, must be one of those same origins; ``Sec-Fetch-Site``, where a browser
  sends it, must be ``same-origin`` or ``none`` (a page on another localhost port is same-site, not same-origin,
  and would otherwise ride on the SameSite=Strict cookie).
* **Session cookie**: an ``HttpOnly``, ``SameSite=Strict`` cookie carrying a random per-run token. The
  browser gets it by opening the one-time launch URL (``/?launch=<secret>``) that ``open_dashboard``
  builds in memory; the secret is spent on first use and expires after a minute.
  A program that merely requests ``/`` gets nothing.

After a restart of NexusVox the token changes; an old dashboard tab is refused until it is reopened
from the tray.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time

from flask import Flask, Response, jsonify, redirect, request

COOKIE_NAME = "nexusvox_session"
LAUNCH_PARAM = "launch"
DENIED_HEADER = "X-NexusVox-Denied"  # set on every refusal; the dashboard script keys on it
LAUNCH_TTL_S = 60.0
_TAB = re.compile(r"[a-z0-9-]{1,32}")

_DENIED_PAGE = (
    "<!doctype html><title>NexusVox</title><body style='font-family:sans-serif;margin:3em'>"
    "<h1>Access denied</h1><p>Open the dashboard from the NexusVox tray icon.</p></body>"
)


class DashboardGuard:
    """Holds the session token and the one-time launch secrets for one dashboard server."""

    def __init__(self, port: int) -> None:
        self._session_token = secrets.token_urlsafe(32)
        self._hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})
        self._origins = frozenset(f"http://{host}" for host in self._hosts)
        self._launch_secrets: dict[str, float] = {}  # secret -> expiry (monotonic seconds)
        self._lock = threading.Lock()

    @property
    def session_token(self) -> str:
        return self._session_token

    def allowed_hosts(self) -> frozenset[str]:
        return self._hosts

    def allowed_origins(self) -> frozenset[str]:
        return self._origins

    def new_launch_secret(self) -> str:
        """A single-use secret that the browser trades for the session cookie."""
        secret = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._launch_secrets = {k: v for k, v in self._launch_secrets.items() if v > now}
            self._launch_secrets[secret] = now + LAUNCH_TTL_S
        return secret

    def _spend(self, secret: str) -> bool:
        with self._lock:
            expiry = self._launch_secrets.pop(secret, None)
        return expiry is not None and expiry > time.monotonic()

    def _has_token(self) -> bool:
        cookie = request.cookies.get(COOKIE_NAME, "")
        return bool(cookie) and hmac.compare_digest(cookie, self._session_token)

    def check(self) -> Response | None:
        """The before_request hook: None lets the request through."""
        if request.host.lower() not in self._hosts:
            return _deny()
        origin = request.headers.get("Origin")
        if origin is not None and origin.lower() not in self._origins:
            return _deny()
        fetch_site = request.headers.get("Sec-Fetch-Site")
        if fetch_site is not None and fetch_site.lower() not in ("same-origin", "none"):
            return _deny()

        launch = request.args.get(LAUNCH_PARAM)
        if launch is not None and request.method == "GET" and request.path == "/":
            tab = request.args.get("tab", "")
            response = redirect("/" + (f"#{tab}" if _TAB.fullmatch(tab) else ""))
            response.set_cookie(COOKIE_NAME, self._session_token, httponly=True, samesite="Strict", path="/")
            if not self._spend(launch):  # last, so a failure above cannot burn the secret
                return _deny()
            return response

        if not self._has_token():
            return _deny()
        return None


def _deny() -> Response:
    """403 as JSON for the API, which the script turns into a reopen-from-tray message, else a small page."""
    if request.path.startswith("/api/"):
        response = jsonify({"error": "Access denied. Open the dashboard from the NexusVox tray icon.", "denied": True})
    else:
        response = Response(_DENIED_PAGE, mimetype="text/html")
    response.status_code = 403
    response.headers[DENIED_HEADER] = "1"
    return response


def install_guard(app: Flask, port: int) -> DashboardGuard:
    guard = DashboardGuard(port)
    app.before_request(guard.check)
    app.extensions["nexusvox_guard"] = guard
    return guard
