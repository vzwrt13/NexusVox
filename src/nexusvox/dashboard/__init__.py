"""Dashboard server using Flask, opened in the default browser."""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
import threading
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from flask import Flask, jsonify
from werkzeug.serving import ThreadedWSGIServer

from ..config import Config
from ..extensions import load_extensions
from ..storage.db import Database
from ..system.single_instance import show_message
from ..transcription.file_transcribe import MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL
from . import (
    routes_analytics,
    routes_commands,
    routes_extensions,
    routes_files,
    routes_meetings,
    routes_models,
    routes_review,
    routes_settings,
    routes_setup,
    routes_vocabulary,
)
from .api import DashboardAPI
from .guard import LAUNCH_PARAM, DashboardGuard, install_guard

if TYPE_CHECKING:
    from ..app.core import NexusVoxApp

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_PORT = 47392  # Uncommon port to avoid collisions.
_FALLBACK_PORTS = tuple(range(47400, 47410))  # used when _PORT is held by another program; 47393 is the remote mic
_port = _PORT  # the port this process serves on; differs from _PORT only after a fallback
_server_started = False
_start_failed = False  # every port was held: the API is built, there is no server, and it is not retried
_api: DashboardAPI | None = None
_guard: DashboardGuard | None = None
_lock = threading.Lock()
# Every topic's routes, registered in this order; `tests/test_dashboard_routes.py` fails on a module left out.
_ROUTE_MODULES = (
    routes_models,
    routes_settings,
    routes_setup,
    routes_meetings,
    routes_commands,
    routes_analytics,
    routes_review,
    routes_vocabulary,
    routes_files,
    routes_extensions,
)


def _listen_socket(port: int) -> socket.socket:
    """A listening socket on 127.0.0.1:`port`, bound by us: werkzeug exits the process when its own bind fails.

    Exclusive on Windows: with SO_REUSEADDR a second socket may share a port another program holds.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def _make_server(port: int, wsgi_app) -> ThreadedWSGIServer:
    sock = _listen_socket(port)
    try:
        # werkzeug duplicates the descriptor, so ours can be closed once it holds its own.
        return ThreadedWSGIServer("127.0.0.1", port, wsgi_app, fd=sock.fileno())
    finally:
        sock.close()


def _bind_server(wsgi_app) -> tuple[int, ThreadedWSGIServer] | None:
    """Bind the first free dashboard port (the usual one, else a fallback) once; None when all are held."""
    for port in (_PORT, *_FALLBACK_PORTS):
        try:
            return port, _make_server(port, wsgi_app)
        except OSError:
            logger.debug("Dashboard port %d is held by another program", port)
    return None


def _create_app(api: DashboardAPI, port: int = _PORT) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR), static_url_path="")
    # Werkzeug refuses a request body above this (413) before it parses a form; the slack covers multipart framing.
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"ok": False, "error": f"File too large (max {MAX_UPLOAD_LABEL})"}), 413

    install_guard(app, port)  # before any route, so the extension blueprints below are covered too

    # Suppress Flask request logging in production.
    wlog = logging.getLogger("werkzeug")
    wlog.setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    # Features that are not part of v1: their routes below are only registered when the flag is on, and the page
    # asks which ones to show. Read once at start, so a change needs a restart.
    experimental = api.experimental

    @app.route("/api/features")
    def get_features():
        return jsonify(asdict(experimental))

    for routes in _ROUTE_MODULES:
        routes.register(app, api)

    return app


def ensure_dashboard_api(db: Database, config: Config, app: NexusVoxApp | None = None) -> DashboardAPI:
    """Start the Flask server (once) without opening the browser, and return its API object."""
    global _server_started, _api, _guard, _port, _start_failed

    with _lock:
        if _api is None:
            # Build the thread→async bridge for model switching
            on_model_switch = None
            get_switch_status = None
            get_learn_status = None
            get_transcriber = None
            on_feedback_mode = None
            if app is not None:

                def on_model_switch(model_id: str) -> None:
                    loop = app._loop
                    if loop is None or loop.is_closed():
                        raise RuntimeError("The app is not running, so the model cannot be switched")
                    coro = app.switch_model(model_id)
                    try:
                        future = asyncio.run_coroutine_threadsafe(coro, loop)
                    except RuntimeError:
                        coro.close()  # never scheduled: no "never awaited" warning
                        raise

                    def settle() -> None:
                        # `switch_model` raises before it settles the status when it refuses (Docker out of
                        # reach, a switch in flight, ...): the page would read the idle status as "done".
                        status = app.get_switch_status()["status"]
                        if status in ("idle", "ready"):
                            app._end_switch("error", str(future.exception()))

                    def on_done(done) -> None:
                        if done.cancelled() or (exc := done.exception()) is None:
                            return
                        logger.error("Model switch from the dashboard failed: %s", exc, exc_info=exc)
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(settle)

                    future.add_done_callback(on_done)

                get_switch_status = app.get_switch_status
                get_learn_status = app.get_learn_status
                on_feedback_mode = app.on_feedback_mode

                def get_transcriber():
                    return app._transcriber

            # Resolve benchmarks dir relative to the project root (CWD).
            benchmarks_dir = Path.cwd() / "benchmarks"

            _api = DashboardAPI(
                db._session_factory,
                config,
                db=db,
                on_model_switch=on_model_switch,
                get_switch_status=get_switch_status,
                get_learn_status=get_learn_status,
                get_transcriber=get_transcriber,
                on_feedback_mode=on_feedback_mode,
                benchmarks_dir=benchmarks_dir,
                extensions=load_extensions(),
            )
            # Independent of the dashboard port: leftovers are swept even when every port is taken.
            threading.Thread(target=_api.sweep_meeting_leftovers, name="meeting-sweep", daemon=True).start()
        if not _server_started and not _start_failed:
            # The Flask app needs the port (host check), the port needs the bind: bind first, hand the
            # requests to the app once it exists.
            flask_holder: list[Flask] = []

            def wsgi_app(environ, start_response):
                return flask_holder[0](environ, start_response)

            bound = _bind_server(wsgi_app)
            if bound is None:
                _start_failed = True
                message = (
                    f"The dashboard could not start: port {_PORT} and the fallback ports {_FALLBACK_PORTS[0]}-"
                    f"{_FALLBACK_PORTS[-1]} are all held by other programs. Dictation still works."
                )
                logger.error(message)
                show_message(message)
            else:
                port, server = bound
                flask_app = _create_app(_api, port)
                flask_holder.append(flask_app)
                _guard = flask_app.extensions["nexusvox_guard"]
                threading.Thread(target=server.serve_forever, daemon=True, name="dashboard-server").start()
                _server_started = True
                _port = port
                if port != _PORT:
                    logger.warning("Dashboard port %d is held by another program; using %d instead", _PORT, port)
                logger.info("Dashboard server started on http://127.0.0.1:%d", port)
        return _api


def running_dashboard_api() -> DashboardAPI | None:
    """The API object of the dashboard server when it has been started, else None."""
    return _api


def open_dashboard(db: Database, config: Config, app: NexusVoxApp | None = None, tab: str = "") -> None:
    """Start the Flask server (once) and open the dashboard in the browser, on `tab` when given."""
    ensure_dashboard_api(db, config, app)
    if not _server_started or _guard is None:
        logger.error("The dashboard server is not running, so the dashboard was not opened")
        return
    query = f"?{LAUNCH_PARAM}={_guard.new_launch_secret()}" + (f"&tab={quote(tab)}" if tab else "")
    webbrowser.open(f"http://127.0.0.1:{_port}/{query}")
