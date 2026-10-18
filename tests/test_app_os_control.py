"""Tests for the os-control side of the transcription cycle: a command recording that is
skipped or fails must still send `::hide`, because `::show` already went out on the key press.

The app is built without `__init__` and its collaborators are mocks, so only the cycle's
control flow runs.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from nexusvox.app import NexusVoxApp
except Exception:  # Windows-only imports (pynput hook, tray, SendInput) on other platforms
    pytest.skip("nexusvox.app needs the Windows runtime", allow_module_level=True)


def _app(*, command: bool, connect_error: Exception | None = None, paste_last: bool = False) -> NexusVoxApp:
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._transcriber = MagicMock()
    app._transcriber.connect = AsyncMock(side_effect=connect_error)
    app._transcriber.disconnect = AsyncMock()
    app._hotkey = MagicMock(command=command, paste_last=paste_last, active=False)
    app._tray = MagicMock()
    app._mic_guard = None
    app._send_os_command = MagicMock(return_value="ok")
    return app


def _run_skipped_cycle(app: NexusVoxApp) -> None:
    async def cycle():
        app._record_stop_event = asyncio.Event()
        app._record_stop_event.set()  # released before the recording could start
        await app._transcription_cycle()

    asyncio.run(cycle())


def test_skipped_command_recording_hides_the_window_numbers():
    app = _app(command=True)
    _run_skipped_cycle(app)
    app._send_os_command.assert_called_once_with("::hide")


def test_skipped_dictation_sends_nothing_to_os_control():
    app = _app(command=False)
    _run_skipped_cycle(app)
    app._send_os_command.assert_not_called()


def test_failed_command_recording_hides_the_window_numbers():
    app = _app(command=True, connect_error=RuntimeError("server down"))
    _run_skipped_cycle(app)
    app._send_os_command.assert_called_once_with("::hide")
    app._transcriber.disconnect.assert_awaited_once()


def test_hide_failure_does_not_escape_the_cycle():
    app = _app(command=True, connect_error=RuntimeError("server down"))
    app._send_os_command.side_effect = ValueError("bad reply")
    _run_skipped_cycle(app)
    app._transcriber.disconnect.assert_awaited_once()


# -- paste last ----------------------------------------------------------------


def _paste_app(last_typed: str) -> NexusVoxApp:
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._last_typed = last_typed
    app._config = MagicMock(injection_delay_ms=0)
    return app


def test_paste_last_pastes_the_last_typed_text(monkeypatch):
    import nexusvox.app as app_module

    pasted = []
    monkeypatch.setattr(app_module, "inject_text", lambda text, delay: pasted.append(text))
    asyncio.run(_paste_app("Bin gleich da")._paste_last_text())
    assert pasted == ["Bin gleich da"]


def test_paste_last_with_nothing_yet_beeps(monkeypatch):
    import nexusvox.app as app_module

    class RunNow:  # threading.Thread stand-in that runs the target at once
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    beeps = []
    monkeypatch.setattr(app_module, "inject_text", lambda *_a: pytest.fail("nothing to paste"))
    monkeypatch.setattr(app_module, "beep_error", lambda: beeps.append("error"))
    monkeypatch.setattr(app_module.threading, "Thread", RunNow)
    asyncio.run(_paste_app("")._paste_last_text())
    assert beeps == ["error"]


def test_quick_paste_tap_still_pastes_when_the_cycle_is_skipped(monkeypatch):
    import nexusvox.app as app_module

    pasted = []
    monkeypatch.setattr(app_module, "inject_text", lambda text, delay: pasted.append(text))
    app = _app(command=False, paste_last=True)
    app._last_typed = "Bin gleich da"
    app._config = MagicMock(injection_delay_ms=0)
    _run_skipped_cycle(app)
    assert pasted == ["Bin gleich da"]
