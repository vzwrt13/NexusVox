"""The voice side of `[experimental]`: OS commands and "nexus flag" do nothing unless their flag is on."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nexusvox.config import Config, ExperimentalConfig

try:
    import nexusvox.app as app_module
except Exception:  # Windows-only imports (pynput hook, tray, SendInput, PortAudio) on other platforms
    pytest.skip("nexusvox.app needs the Windows runtime", allow_module_level=True)


def _app(flags: ExperimentalConfig):
    app = app_module.NexusVoxApp.__new__(app_module.NexusVoxApp)
    app._config = Config(experimental=flags)
    app._config.os_commands.enabled = True
    app._config.os_commands.apps = {"chrome": "chrome.exe"}
    app._cue = MagicMock()
    return app


def _run(app, text, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "execute_nexus_command", lambda cmd, cfg: calls.append(cmd.action) or True)
    cmd = app_module.parse_nexus_command(text)
    label = asyncio.run(app._run_command(text, cmd, []))
    return label, calls


def test_os_commands_do_not_run_by_default_even_when_enabled_in_the_old_section(monkeypatch):
    label, calls = _run(_app(ExperimentalConfig()), "nexus minimize chrome", monkeypatch)
    assert label is None and calls == []


def test_os_commands_run_with_the_flag(monkeypatch):
    label, calls = _run(_app(ExperimentalConfig(os_commands=True)), "nexus minimize chrome", monkeypatch)
    assert label == "[nexus] minimize chrome" and calls == ["minimize"]


def test_the_flag_command_is_never_an_os_command(monkeypatch):
    label, calls = _run(_app(ExperimentalConfig(os_commands=True)), "nexus flag", monkeypatch)
    assert label is None and calls == []


def _flag_cycle(monkeypatch, flags):
    from test_app_os_control import _dictation_app, _run_cycle  # noqa: PLC0415

    app, _ = _dictation_app(monkeypatch, lambda *a, **k: None)
    app._config = Config(experimental=flags)
    app._config.injection_delay_ms = 0
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="nexus flag", confidence=None))
    app._run_command = AsyncMock(return_value=None)
    app._flag_last_transcription = MagicMock()
    _run_cycle(app)
    return app


def test_nexus_flag_is_typed_like_any_text_by_default(monkeypatch):
    app = _flag_cycle(monkeypatch, ExperimentalConfig())
    app._flag_last_transcription.assert_not_called()
    app._run_command.assert_awaited_once()


def test_nexus_flag_flags_with_the_flag(monkeypatch):
    app = _flag_cycle(monkeypatch, ExperimentalConfig(flag_command=True))
    app._flag_last_transcription.assert_called_once()
    app._run_command.assert_not_awaited()
