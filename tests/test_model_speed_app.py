"""The polish row stores the language model and the transcription device it ran with."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from app_parts import patch_app

from nexusvox.config import Config, LlmConfig

try:
    from nexusvox.app.core import NexusVoxApp
    from nexusvox.app.records import _Recording
except Exception:  # Windows-only imports (pynput hook, tray, SendInput) on other platforms
    pytest.skip("nexusvox.app.core needs the Windows runtime", allow_module_level=True)


def _app(tmp_path):
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._config.llm = LlmConfig(url="http://llm:1234", model="qwen")
    app._config.database.path = str(tmp_path / "db.sqlite")
    app._db = MagicMock()
    app._store_audio = MagicMock()
    app._tray = MagicMock()
    app._transcriber = MagicMock(model="m", device="cpu")
    return app


def test_a_polish_row_stores_the_model_and_the_device(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    app = _app(tmp_path)
    recording = _Recording(bytearray(), 10, None, 1, "cpu")

    asyncio.run(
        app._save_polish(
            recording, "polish", "default", "", ok=True, status="applied", model_ms=900, llm=("qwen", "http://llm:1234")
        )
    )

    kwargs = app._db.save_transcription.call_args.kwargs
    assert (kwargs["llm_model"], kwargs["llm_url"], kwargs["transcribe_device"]) == ("qwen", "http://llm:1234", "cpu")


def test_the_device_is_the_one_read_when_the_transcript_arrived(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    app = _app(tmp_path)
    recording = _Recording(bytearray(), 10, None, 1, "gpu")
    app._transcriber.device = "cpu"  # the auto engine switched for the next take meanwhile

    app._save_command(recording, "[nexus] snap left", "snap left")

    assert app._db.save_transcription.call_args.kwargs["transcribe_device"] == "gpu"


def test_an_extension_row_names_no_model(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    app = _app(tmp_path)

    asyncio.run(app._save_polish(None, "extension", "x", "", ok=True, status="extension", model_ms=None))

    kwargs = app._db.save_transcription.call_args.kwargs
    assert (kwargs["llm_model"], kwargs["llm_url"], kwargs["transcribe_device"]) == (None, None, None)


def test_an_extension_whose_paste_failed_still_names_no_model(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    patch_app(monkeypatch, "foreground_window", lambda: 2)  # the focus moved away from window 1
    app = _app(tmp_path)
    app._cue = MagicMock()
    app._last_typed = ""

    asyncio.run(
        app._paste_over_selection("new", "old", 1, "extension", "x", "x", None, status="extension", model_ms=None)
    )

    kwargs = app._db.save_transcription.call_args.kwargs
    assert kwargs["polish_status"] == "failed"
    assert (kwargs["llm_model"], kwargs["llm_url"]) == (None, None)


def test_polish_credits_the_model_the_request_went_to(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    patch_app(monkeypatch, "read_selection", lambda *a: "some selected text")
    patch_app(monkeypatch, "foreground_window", lambda: 1)
    patch_app(monkeypatch, "loaded_extensions", lambda: None)
    app = _app(tmp_path)
    app._config.llm = LlmConfig(url="http://llm:1234", model="gemma")
    app._db.get_polish_presets.return_value = []
    app._cue = MagicMock()
    app._edit_watcher = MagicMock()

    def slow_polish(selection, instruction, llm):
        # The user switches the language model in the dashboard while the request is out.
        app._config.llm = LlmConfig(url="http://other:1", model="qwen")
        return "rewritten text"

    patch_app(monkeypatch, "polish", slow_polish)
    patch_app(monkeypatch, "inject_text", lambda *a: None)

    asyncio.run(app._polish_selection("shorter", None))

    kwargs = app._db.save_transcription.call_args.kwargs
    assert (kwargs["llm_model"], kwargs["llm_url"]) == ("gemma", "http://llm:1234")
