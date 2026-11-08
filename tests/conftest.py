"""Shared test fixtures for NexusVox test suite."""

from __future__ import annotations

import io
import logging
import socket
import sys
import wave

import pytest

from nexusvox.config import Config, DatabaseConfig, ExperimentalConfig
from nexusvox.dashboard import _PORT, _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.dashboard.guard import COOKIE_NAME
from nexusvox.storage.db import Database


@pytest.fixture(autouse=True)
def _tests_never_reach_a_real_llm_server(monkeypatch):
    """A test that connects to Ollama's port fails instead of reaching the user's server.

    Code that talks to an LLM (`/api/chat` loads the model into VRAM and keeps it there, `/api/tags` and `/api/ps`
    only read) must be mocked with a fake server on another port or a patched `urlopen`; a missed mock used to
    load the user's model as a side effect of running the suite.
    """
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def is_ollama(address) -> bool:
        return isinstance(address, tuple) and len(address) >= 2 and address[1] == 11434

    def connect(self, address):
        if is_ollama(address):
            raise ConnectionRefusedError("tests must not reach a real Ollama (port 11434); mock the LLM call")
        return real_connect(self, address)

    def connect_ex(self, address):
        return 111 if is_ollama(address) else real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Run every test in its own folder: ``save_config`` and ``load_config`` default to ``config.toml`` in the
    working directory, and a test that saved there left a config (with a throw-away database path) in the
    checkout that the app then loaded."""
    monkeypatch.chdir(tmp_path)


def pytest_collection_modifyitems(config, items):
    """Tests marked ``desktop`` drive the real desktop (clipboard, windows); they run only with ``-m desktop``."""
    if "desktop" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="needs a real desktop; run with -m desktop")
    for item in items:
        if "desktop" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _tests_never_touch_the_real_log(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a throw-away folder and drop any file handler a test leaves on the root logger.

    ``nexusvox.__main__.main`` attaches a rotating file handler to the root logger. Left in place, every later
    test that logs ("boom", "dummy-tab", ...) wrote into the user's real ``nexusvox.log``.
    """
    # Every place the app resolves a per-user folder from, including the fallbacks to the home folder.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in list(root.handlers):
        if handler not in before and isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _tests_never_play_sound(monkeypatch):
    """No test makes the speakers chime: the app's start/stop/error beeps go through ``sounddevice.play``."""
    try:
        import sounddevice
    except (ImportError, OSError):  # no PortAudio on this runner: nothing can play
        return
    monkeypatch.setattr(sounddevice, "play", lambda *args, **kwargs: None)
    monkeypatch.setattr(sounddevice, "wait", lambda *args, **kwargs: None)


WINDOW_ACTIONS = ("open_app", "close_app", "focus_app", "fullscreen_app", "minimize_app", "snap_app")


@pytest.fixture(autouse=True)
def _tests_never_touch_real_windows(monkeypatch):
    """A test that reaches a real window action fails instead of acting: `close_app` ends in `taskkill /f`.

    A mock aimed at a stale path raises nothing by itself, so this is what stops the call. `setattr` fails for an
    action that was renamed, which keeps the list honest.
    """
    try:
        from nexusvox.system import window_manager
    except (ImportError, AttributeError):  # `ctypes.windll` exists on Windows only: no windows to touch here
        return

    def blocked(*args, **kwargs):
        raise AssertionError(f"a test reached a real window action: {args}")

    for action in WINDOW_ACTIONS:
        monkeypatch.setattr(window_manager, action, blocked)


@pytest.fixture(autouse=True)
def _dashboard_test_clients_are_signed_in(monkeypatch):
    """``app.test_client()`` talks to the dashboard like the real page: right Host, session cookie set.

    The access checks themselves are tested in ``test_dashboard_guard.py``, which passes its own host.
    """
    from flask import Flask
    from flask.testing import FlaskClient

    original_client = Flask.test_client
    original_open = FlaskClient.open

    def signed_in(self, *args, **kwargs):
        client = original_client(self, *args, **kwargs)
        guard = self.extensions.get("nexusvox_guard")
        if guard is not None:
            client.set_cookie(COOKIE_NAME, guard.session_token, domain="127.0.0.1")
        return client

    def open_on_dashboard_host(self, *args, **kwargs):
        if getattr(self.application, "extensions", {}).get("nexusvox_guard") is None:
            return original_open(self, *args, **kwargs)  # not a dashboard app
        if args and not isinstance(args[0], str):
            return original_open(self, *args, **kwargs)  # a prepared builder/request keeps its own host
        kwargs.setdefault("base_url", f"http://127.0.0.1:{_PORT}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Flask, "test_client", signed_in)
    monkeypatch.setattr(FlaskClient, "open", open_on_dashboard_host)


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite database for each test.

    A file, not ``:memory:``: an in-memory database exists once per connection, and the file
    transcription worker thread opens its own.
    """
    config = DatabaseConfig(path=str(tmp_path / "test.db"))
    return Database(config)


@pytest.fixture
def session_factory(db):
    """SQLAlchemy session factory from the in-memory database."""
    return db._session_factory


@pytest.fixture
def sample_transcriptions(db):
    """Seed the database with 8 varied transcription records."""
    records = []

    data = [
        ("Hello world this is a test", "en", 2500, None),
        ("Das ist ein deutscher Test", "de", 3200, None),
        ("The quick brown fox jumps over the lazy dog", "en", 4100, 0.92),
        ("Testing speech to text accuracy", "en", 1800, 0.87),
        ("Noch ein deutscher Satz zum Testen", "de", 2900, None),
        ("Multiple words repeated words for testing words", "en", 3500, None),
        ("Short test", "en", 800, 0.95),
        ("Langer deutscher Satz mit vielen Woertern zum Testen der Erkennung", "de", 5200, None),
    ]

    for text, lang, duration, confidence in data:
        record = db.save_transcription(text, lang, duration, confidence)
        records.append(record)

    # Flag one record and add a correction to another
    db.flag_transcription(records[1].id)
    db.flag_transcription(records[5].id)
    db.update_correction(records[5].id, "Multiple words repeated words for testing purposes")

    return records


def _patch_api(monkeypatch, name, value):
    """Replace `name` in every topic module of `DashboardAPI` that imported it, not just in one of them."""
    modules = [sys.modules[cls.__module__] for cls in DashboardAPI.__mro__]
    holders = [module for module in modules if hasattr(module, name)]
    assert holders, f"no module of DashboardAPI has {name}"
    for module in holders:
        monkeypatch.setattr(module, name, value)


@pytest.fixture
def patch_api(monkeypatch):
    """`patch_api(name, value)`: for a name several topic modules import, such as `cuda_available`."""
    return lambda name, value: _patch_api(monkeypatch, name, value)


def _stub_save_config(monkeypatch):
    _patch_api(monkeypatch, "save_config", lambda cfg, path=None: None)


@pytest.fixture
def no_config_save(monkeypatch):
    """The dashboard saves settings without touching the real config file."""
    _stub_save_config(monkeypatch)


def _dashboard_client(db, session_factory, monkeypatch, config):
    api = DashboardAPI(session_factory, config, db=db)
    app = _create_app(api)
    app.config["TESTING"] = True

    # Prevent save_config from writing to real config file
    _stub_save_config(monkeypatch)
    # Make model-listing / switching tests deterministic across GPU-less CI.
    monkeypatch.setattr(
        "nexusvox.dashboard.api_models.resolve_device",
        lambda d: "cuda" if d in ("auto", "cuda") else "cpu",
    )

    with app.test_client() as client:
        yield client


@pytest.fixture
def flask_client(db, session_factory, monkeypatch):
    """Flask test client backed by the in-memory database, with the default config (experimental features off)."""
    yield from _dashboard_client(db, session_factory, monkeypatch, Config())


@pytest.fixture
def experimental_client(db, session_factory, monkeypatch):
    """Like ``flask_client``, with every `[experimental]` feature on (OS commands, Edit tab, GitHub, Dev)."""
    flags = ExperimentalConfig(os_commands=True, flag_command=True, dev_tab=True)
    yield from _dashboard_client(db, session_factory, monkeypatch, Config(experimental=flags))


@pytest.fixture
def sample_wav_bytes():
    """Generate a minimal valid 16kHz mono PCM16 WAV (0.5s of silence)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16_000)
        wf.writeframes(b"\x00\x00" * 8_000)  # 0.5s
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_pin_probe(monkeypatch):
    """A never-expire request first asks Ollama's /api/ps who loaded the model; tests that fake `urlopen` for the
    chat call must not see that second request. tests/test_llm_keep_alive.py puts the real one back."""
    from nexusvox import llm

    monkeypatch.setattr(llm, "_note_pinned", lambda config: None)
