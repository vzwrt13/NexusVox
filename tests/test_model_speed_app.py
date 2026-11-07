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
    recording = _Recording(bytearray(), 10, None, 1)

    asyncio.run(app._save_polish(recording, "polish", "default", "", ok=True, status="applied", model_ms=900))

    kwargs = app._db.save_transcription.call_args.kwargs
    assert (kwargs["llm_model"], kwargs["llm_url"], kwargs["transcribe_device"]) == ("qwen", "http://llm:1234", "cpu")


def test_an_extension_row_names_no_model(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    app = _app(tmp_path)

    asyncio.run(app._save_polish(None, "extension", "x", "", ok=True, status="extension", model_ms=None))

    kwargs = app._db.save_transcription.call_args.kwargs
    assert (kwargs["llm_model"], kwargs["llm_url"], kwargs["transcribe_device"]) == (None, None, None)
