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
    from nexusvox.hotkey import COMMAND, PASTE_LAST, POLISH
except Exception:  # Windows-only imports (pynput hook, tray, SendInput) on other platforms
    pytest.skip("nexusvox.app needs the Windows runtime", allow_module_level=True)


def _app(
    *, command: bool, connect_error: Exception | None = None, paste_last: bool = False, polish: bool = False
) -> NexusVoxApp:
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._transcriber = MagicMock()
    app._transcriber.connect = AsyncMock(side_effect=connect_error)
    app._transcriber.disconnect = AsyncMock()
    mark = COMMAND if command else PASTE_LAST if paste_last else POLISH if polish else None
    app._hotkey = MagicMock(command=command, mark=mark, active=False)
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

    beeps = []
    monkeypatch.setattr(app_module, "inject_text", lambda *_a: pytest.fail("nothing to paste"))
    monkeypatch.setattr(app_module, "beep_error", lambda: beeps.append("error"))
    monkeypatch.setattr(app_module, "_play", lambda beep: beep())
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


# -- polish ----------------------------------------------------------------------


def _polish_app(monkeypatch, selection: str, rewritten: str | Exception, windows=(1, 1)):
    import nexusvox.app as app_module

    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = MagicMock(injection_delay_ms=0)
    app._last_typed = ""
    app._save_command = MagicMock()
    calls = {"pasted": [], "beeps": []}

    def fake_polish(sel, instruction, cfg):
        if isinstance(rewritten, Exception):
            raise rewritten
        return rewritten

    monkeypatch.setattr(app_module, "read_selection", lambda delay, in_terminals: selection)
    focus = iter(windows)
    monkeypatch.setattr(app_module, "foreground_window", lambda: next(focus))
    monkeypatch.setattr(app_module, "polish", fake_polish)
    monkeypatch.setattr(app_module, "inject_text", lambda text, delay: calls["pasted"].append(text))
    monkeypatch.setattr(app_module, "beep_error", lambda: calls["beeps"].append("error"))
    monkeypatch.setattr(app_module, "_play", lambda beep: beep())
    return app, calls


def test_polish_pastes_the_rewritten_selection(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Sehr geehrte Damen und Herren")
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert calls == {"pasted": ["Sehr geehrte Damen und Herren"], "beeps": []}
    assert app._last_typed == "Sehr geehrte Damen und Herren"
    assert app._save_command.call_args.args[1] == "[polish] mach es formeller"


def test_polish_without_a_selection_beeps(monkeypatch):
    app, calls = _polish_app(monkeypatch, "", "unused")
    asyncio.run(app._polish_selection("", MagicMock()))
    assert calls == {"pasted": [], "beeps": ["error"]}


def test_polish_with_the_model_down_keeps_the_selection(monkeypatch):
    from nexusvox.llm import LlmError

    app, calls = _polish_app(monkeypatch, "hi leute", LlmError("refused"))
    asyncio.run(app._polish_selection("", MagicMock()))
    assert calls == {"pasted": [], "beeps": ["error"]}
    app._save_command.assert_not_called()


def test_polish_does_not_paste_when_the_focus_moved(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo zusammen", windows=(1, 2))
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert calls == {"pasted": [], "beeps": ["error"]}
    assert app._last_typed == "Hallo zusammen"  # the paste key still pastes it


def test_polish_beeps_when_the_clipboard_is_busy(monkeypatch):
    import nexusvox.app as app_module
    from nexusvox.clipboard import ClipboardError

    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo zusammen")

    def busy(text, delay):
        raise ClipboardError("OpenClipboard failed")

    monkeypatch.setattr(app_module, "inject_text", busy)
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert calls["beeps"] == ["error"]
    assert app._last_typed == "Hallo zusammen"  # the paste key still pastes it


def test_polish_beeps_when_the_selection_cannot_be_copied(monkeypatch):
    import nexusvox.app as app_module
    from nexusvox.clipboard import ClipboardError

    app, calls = _polish_app(monkeypatch, "unused", "unused")

    def busy(delay, in_terminals):
        raise ClipboardError("OpenClipboard failed")

    monkeypatch.setattr(app_module, "read_selection", busy)
    asyncio.run(app._polish_selection("", MagicMock()))
    assert calls == {"pasted": [], "beeps": ["error"]}


def test_quick_tap_of_the_polish_chord_polishes_with_the_default_instruction():
    app = _app(command=False, polish=True)
    app._polish_selection = AsyncMock()
    _run_skipped_cycle(app)
    app._polish_selection.assert_awaited_once_with("", None)
