"""Tests for the os-control side of the transcription cycle: a command recording that is
skipped or fails must still send `::hide`, because `::show` already went out on the key press.

The app is built without `__init__` and its collaborators are mocks, so only the cycle's
control flow runs.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from nexusvox.app import NexusVoxApp
    from nexusvox.extensions import API_VERSION, ActiveApp, Extension, ExtensionRegistry
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


def _polish_app(monkeypatch, selection: str, rewritten: str | Exception, windows=(1, 1), presets=()):
    import nexusvox.app as app_module

    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = MagicMock(injection_delay_ms=0)
    app._last_typed = ""
    app._save_command = MagicMock()
    app._db = MagicMock(get_polish_presets=MagicMock(return_value=list(presets)))
    calls = {"pasted": [], "beeps": []}

    def fake_polish(sel, instruction, cfg):
        calls["instruction"] = instruction
        if isinstance(rewritten, Exception):
            raise rewritten
        return rewritten

    monkeypatch.setattr(app_module, "read_selection", lambda delay, in_terminals: selection)
    focus = iter(windows)
    monkeypatch.setattr(app_module, "foreground_window", lambda: next(focus))
    monkeypatch.setattr(app_module, "polish", fake_polish)
    monkeypatch.setattr(NexusVoxApp, "_active_app", staticmethod(lambda: ActiveApp("Code.exe", "main.py")))
    monkeypatch.setattr(app_module, "loaded_extensions", lambda: ExtensionRegistry())
    monkeypatch.setattr(app_module, "inject_text", lambda text, delay: calls["pasted"].append(text))
    monkeypatch.setattr(app_module, "beep_error", lambda: calls["beeps"].append("error"))
    monkeypatch.setattr(app_module, "_play", lambda beep: beep())
    return app, calls


def test_polish_pastes_the_rewritten_selection(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Sehr geehrte Damen und Herren")
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert (calls["pasted"], calls["beeps"]) == (["Sehr geehrte Damen und Herren"], [])
    assert app._last_typed == "Sehr geehrte Damen und Herren"
    assert app._save_command.call_args.args[1] == "[polish] mach es formeller"


def test_polish_uses_a_spoken_preset_and_saves_its_name(monkeypatch):
    from nexusvox.polish_presets import PolishPreset

    preset = PolishPreset("formelle Mail", "Schreib das als formelle E-Mail.")
    app, calls = _polish_app(monkeypatch, "hi leute", "Sehr geehrte Damen und Herren", presets=[preset])
    asyncio.run(app._polish_selection("Formelle Mail", MagicMock()))
    assert calls["instruction"] == "Schreib das als formelle E-Mail."
    assert app._save_command.call_args.args[1:] == ("[polish] formelle Mail", "Formelle Mail")


def test_polish_without_an_instruction_uses_the_default_preset(monkeypatch):
    from nexusvox.polish_presets import PolishPreset

    preset = PolishPreset("kürzer", "Mach den Text kürzer.", is_default=True)
    app, calls = _polish_app(monkeypatch, "hi leute", "hi", presets=[preset])
    asyncio.run(app._polish_selection("", None))
    assert calls["instruction"] == "Mach den Text kürzer."


def test_polish_without_a_selection_beeps(monkeypatch):
    app, calls = _polish_app(monkeypatch, "", "unused")
    asyncio.run(app._polish_selection("", MagicMock()))
    assert calls == {"pasted": [], "beeps": ["error"]}


def test_polish_with_the_model_down_keeps_the_selection(monkeypatch):
    from nexusvox.llm import LlmError

    app, calls = _polish_app(monkeypatch, "hi leute", LlmError("refused"))
    asyncio.run(app._polish_selection("", MagicMock()))
    assert (calls["pasted"], calls["beeps"]) == ([], ["error"])
    app._save_command.assert_not_called()


def test_polish_does_not_paste_when_the_focus_moved(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo zusammen", windows=(1, 2))
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert (calls["pasted"], calls["beeps"]) == ([], ["error"])
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


def _dictation_app(monkeypatch, inject):
    """An app whose next cycle records "hallo welt" and pastes it through `inject`."""
    import nexusvox.app as app_module
    from nexusvox.cleanup import CleanupResult
    from nexusvox.pipeline import ProcessedText
    from nexusvox.translator import TranslateResult

    app = _app(command=False)
    app._config = MagicMock(injection_delay_ms=0)
    app._config.learn_from_edits.enabled = True
    app._last_typed = ""
    app._audio = MagicMock()
    app._audio.start.side_effect = lambda: app._record_stop_event.set()  # released right away
    app._stream_audio = AsyncMock(return_value=bytearray())
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="hallo welt", confidence=None))
    app._run_command = AsyncMock(return_value=None)
    app._db = MagicMock()
    app._store_audio = MagicMock()
    app._language_of = MagicMock(return_value="de")
    app._edit_watcher = MagicMock()
    processed = ProcessedText(
        "Hallo Welt",
        "Hallo Welt",
        False,
        CleanupResult("Hallo Welt"),
        TranslateResult("Hallo Welt"),
        True,
        "Hallo Welt",
    )
    monkeypatch.setattr(app_module, "process_text", lambda *a, **k: processed)
    monkeypatch.setattr(app_module, "inject_text", inject)
    beeps = []
    monkeypatch.setattr(app_module, "beep_error", lambda: beeps.append("error"))
    # CI runners have no audio device: a real start/stop beep would raise and end the cycle.
    monkeypatch.setattr(app_module, "beep_start", lambda: None)
    monkeypatch.setattr(app_module, "beep_stop", lambda: None)
    monkeypatch.setattr(app_module, "_play", lambda beep: beep())
    return app, beeps


def _run_cycle(app: NexusVoxApp) -> None:
    async def cycle():
        app._record_stop_event = asyncio.Event()
        await app._transcription_cycle()

    asyncio.run(cycle())


def test_dictation_is_pasted_and_watched_for_edits(monkeypatch):
    pasted = []
    app, beeps = _dictation_app(monkeypatch, lambda text, delay, enter: pasted.append(text))
    _run_cycle(app)
    assert (pasted, beeps) == (["Hallo Welt"], [])
    app._edit_watcher.watch.assert_called_once_with("Hallo Welt", app._config.learn_from_edits.window_s, "Hallo Welt")


def test_dictation_stores_the_process_name_of_the_target_app_only(monkeypatch):
    from nexusvox.extensions import ActiveApp

    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: 0.0)
    monkeypatch.setattr(app, "_active_app", lambda: ActiveApp("Outlook.EXE", "Inbox - secret subject"))
    _run_cycle(app)
    kwargs = app._db.save_transcription.call_args.kwargs
    assert kwargs["app"] == "outlook.exe"
    assert "secret" not in str(kwargs)


def test_dictation_the_clipboard_refused_is_not_credited_to_an_app(monkeypatch):
    from nexusvox.clipboard import ClipboardError
    from nexusvox.extensions import ActiveApp

    def busy(text, delay, enter):
        raise ClipboardError("OpenClipboard failed")

    app, _ = _dictation_app(monkeypatch, busy)
    monkeypatch.setattr(app, "_active_app", lambda: ActiveApp("outlook.exe", ""))
    _run_cycle(app)
    assert app._db.save_transcription.call_args.kwargs["app"] is None


def test_a_command_row_is_marked_so_analytics_can_skip_it():
    from nexusvox.models import COMMAND_APP

    app = NexusVoxApp.__new__(NexusVoxApp)
    app._db, app._transcriber, app._config = MagicMock(), MagicMock(), MagicMock()
    app._store_audio = MagicMock()
    app._db.save_transcription.return_value.id = 1
    app._save_command(MagicMock(), "[nexus] open x", "open x")
    assert app._db.save_transcription.call_args.kwargs["app"] == COMMAND_APP


def test_dictation_stores_no_app_when_it_cannot_be_read(monkeypatch):
    from nexusvox.extensions import ActiveApp

    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: 0.0)
    monkeypatch.setattr(app, "_active_app", lambda: ActiveApp())
    _run_cycle(app)
    assert app._db.save_transcription.call_args.kwargs["app"] is None


def test_dictation_beeps_and_is_kept_when_the_clipboard_is_busy(monkeypatch):
    from nexusvox.clipboard import ClipboardError

    def busy(text, delay, enter):
        raise ClipboardError("OpenClipboard failed")

    app, beeps = _dictation_app(monkeypatch, busy)
    _run_cycle(app)
    assert beeps == ["error"]
    assert app._last_typed == "Hallo Welt"  # the paste key pastes it
    app._db.save_transcription.assert_called_once()
    app._edit_watcher.watch.assert_not_called()
    app._edit_watcher.skip.assert_called_once_with("clipboard")


def test_paste_last_beeps_when_the_clipboard_is_busy(monkeypatch):
    import nexusvox.app as app_module
    from nexusvox.clipboard import ClipboardError

    def busy(text, delay):
        raise ClipboardError("OpenClipboard failed")

    app, calls = _polish_app(monkeypatch, "unused", "unused")
    app._last_typed = "Hallo Welt"
    monkeypatch.setattr(app_module, "inject_text", busy)
    asyncio.run(app._paste_last_text())
    assert calls["beeps"] == ["error"]


def test_latency_ends_when_the_text_is_in_not_after_the_clipboard_restore(monkeypatch):
    import time

    # inject_text reports when the text was in place; anything after it (the clipboard
    # restore) is not latency. A time far in the future shows the reported one is used.
    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: time.monotonic() + 60)
    _run_cycle(app)
    assert app._db.save_transcription.call_args.kwargs["latency_ms"] >= 60_000


def test_polish_lets_an_extension_handle_the_spoken_command(monkeypatch):
    class Upper(Extension):
        name = "upper"
        api_version = API_VERSION

        def on_selection_command(self, selection, command, app):
            seen.append((selection, command, app))
            return selection.upper() if command == "shout" else None

    seen: list = []
    app, calls = _polish_app(monkeypatch, "hi leute", RuntimeError("the model must not run"))
    registry = ExtensionRegistry()
    registry.add(Upper())
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: registry)

    asyncio.run(app._polish_selection("shout", MagicMock()))
    assert calls["pasted"] == ["HI LEUTE"]
    assert seen == [("hi leute", "shout", ActiveApp("Code.exe", "main.py"))]
    assert app._last_typed == "HI LEUTE"
    assert app._save_command.call_args.args[1] == "[extension] shout"

    # A command the extension does not know falls through to the language model.
    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo zusammen")
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: registry)
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert calls["pasted"] == ["Hallo zusammen"]


def test_a_slow_extension_answer_is_pasted_without_a_second_polish(monkeypatch):
    class Slow(Extension):
        name = "slow"
        api_version = API_VERSION

        def on_selection_command(self, selection, command, app):
            time.sleep(0.2)  # longer than HOOK_TIMEOUT_S below, shorter than the selection command budget
            return "LATE"

    monkeypatch.setattr("nexusvox.extensions.HOOK_TIMEOUT_S", 0.05)
    app, calls = _polish_app(monkeypatch, "hi leute", RuntimeError("the model must not run"))
    registry = ExtensionRegistry()
    registry.add(Slow())
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: registry)
    asyncio.run(app._polish_selection("go", MagicMock()))
    assert calls["pasted"] == ["LATE"]


def test_a_selection_hook_over_its_budget_falls_back_to_the_core_polish(monkeypatch):
    class Slow(Extension):
        name = "slow"
        api_version = API_VERSION

        def on_selection_command(self, selection, command, app):
            time.sleep(0.3)
            return "LATE"

    monkeypatch.setattr("nexusvox.extensions.SELECTION_COMMAND_TIMEOUT_S", 0.05)
    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo zusammen")
    registry = ExtensionRegistry()
    registry.add(Slow())
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: registry)
    asyncio.run(app._polish_selection("go", MagicMock()))
    assert calls["pasted"] == ["Hallo zusammen"]


def test_an_extension_paste_is_dropped_when_the_focus_moved_during_the_hook(monkeypatch):
    class Slow(Extension):
        name = "slow"
        api_version = API_VERSION

        def on_selection_command(self, selection, command, app):
            return "REPLACED"

    app, calls = _polish_app(monkeypatch, "hi leute", RuntimeError("no model"), windows=(1, 2))
    registry = ExtensionRegistry()
    registry.add(Slow())
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: registry)
    asyncio.run(app._polish_selection("go", MagicMock()))
    assert (calls["pasted"], calls["beeps"]) == ([], ["error"])


def test_dictation_skips_extensions_while_they_still_load_or_when_none_is_installed(monkeypatch):
    app = NexusVoxApp.__new__(NexusVoxApp)
    monkeypatch.setattr(
        NexusVoxApp, "_active_app", staticmethod(lambda: pytest.fail("the active app must not be read"))
    )
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: None)
    assert app._extend_dictation("hi") == "hi"
    monkeypatch.setattr("nexusvox.app.loaded_extensions", lambda: ExtensionRegistry())
    assert app._extend_dictation("hi") == "hi"


def test_a_dictation_take_is_reported_to_the_meeting_from_the_key_press_to_the_stop(monkeypatch):
    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: None)
    calls = []
    app._begin_take = lambda start: calls.append(("begin", start)) or 7
    app._end_take = lambda token, start, end: calls.append(("end", token, start, end))
    app._press_time = time.monotonic() - 2.0  # the connection took two seconds

    _run_cycle(app)

    assert [c[0] for c in calls] == ["begin", "end"]  # once each, the end after the stop
    begin, end = calls
    assert end[1] == 7 and end[2] == begin[1] == app._press_time and end[3] > end[2] + 1.9


def test_a_take_is_reported_even_when_the_cycle_fails_after_the_recording_started(monkeypatch):
    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: None)
    calls = []
    app._begin_take = lambda start: calls.append("begin") or 1
    app._end_take = lambda token, start, end: calls.append("end")
    app._stream_audio = AsyncMock(side_effect=RuntimeError("device lost"))

    _run_cycle(app)

    assert calls == ["begin", "end"]
