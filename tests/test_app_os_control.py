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


@pytest.fixture(autouse=True)
def _silent_cues(monkeypatch):
    """No real chimes (CI has no audio device); tests that count cues patch `_play` again."""
    import nexusvox.app as app_module

    monkeypatch.setattr(app_module, "_play", lambda beep: None)


def _app(
    *, command: bool, connect_error: Exception | None = None, paste_last: bool = False, polish: bool = False
) -> NexusVoxApp:
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._transcriber = MagicMock()
    app._last_typed_rewritten = False
    app._edit_watcher = MagicMock()
    app._transcriber.connect = AsyncMock(side_effect=connect_error)
    app._transcriber.disconnect = AsyncMock()
    mark = COMMAND if command else PASTE_LAST if paste_last else POLISH if polish else None
    app._hotkey = MagicMock(command=command, mark=mark, active=False, mode="hold")
    app._stop_info = (mark, False, None)  # what the stop callback recorded for this take
    app._config = MagicMock(hotkey=MagicMock(max_recording_s=600))
    app._overlay = None
    app._press_time = time.monotonic()
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
    app._last_typed_rewritten = False
    app._edit_watcher = MagicMock()
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
    app._last_typed_rewritten = False
    app._save_command = MagicMock()
    app._edit_watcher = MagicMock()
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
    kwargs = app._save_command.call_args.kwargs
    assert kwargs["polish_status"] == "applied" and kwargs["polish_ms"] >= 0


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
    assert app._save_command.call_args.kwargs["polish_status"] == "failed"
    assert app._save_command.call_args.args[1] == "[polish failed] default"


def test_polish_with_a_slow_model_is_saved_as_a_timeout(monkeypatch):
    from nexusvox.llm import LlmError

    app, _calls = _polish_app(monkeypatch, "hi leute", LlmError("slow", timed_out=True))
    asyncio.run(app._polish_selection("", MagicMock()))
    assert app._save_command.call_args.kwargs["polish_status"] == "timeout"


def test_polish_tap_with_nothing_spoken_is_saved_without_a_recording(monkeypatch):
    app, _calls = _polish_app(monkeypatch, "hi leute", "Hi Leute")
    asyncio.run(app._polish_selection("", None))
    args, kwargs = app._save_command.call_args
    assert args == (None, "[polish] default", "")
    assert kwargs["polish_status"] == "applied"


def test_polish_does_not_paste_when_the_focus_moved(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo zusammen", windows=(1, 2))
    asyncio.run(app._polish_selection("mach es formeller", MagicMock()))
    assert (calls["pasted"], calls["beeps"]) == ([], ["error"])
    assert app._last_typed == "Hallo zusammen"  # the paste key still pastes it
    assert app._save_command.call_args.args[1] == "[polish failed] mach es formeller"
    assert app._save_command.call_args.kwargs["polish_status"] == "failed"  # nothing was pasted


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
    app._config.hotkey.max_recording_s = 600
    app._last_typed = ""
    app._audio = MagicMock()
    app._audio.start.side_effect = lambda: app._record_stop_event.set()  # released right away
    app._stream_audio = AsyncMock(return_value=bytearray())
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="hallo welt", confidence=None))
    app._run_command = AsyncMock(return_value=None)
    app._db = MagicMock()
    app._store_audio = MagicMock()
    monkeypatch.setattr(app_module, "stored_language", lambda _text: "de")
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


# -- Escape cancel, the recording cap, Toggle takes, server down ------------------------


def _record_app(monkeypatch, stops, *, mode="hold", cap=600):
    """A dictation app whose recording runs until the test sets the stop event itself."""
    app, beeps = _dictation_app(monkeypatch, lambda text, delay, enter: pytest.fail("nothing may be pasted"))
    app._audio.start.side_effect = None
    app._stop_info = stops
    app._hotkey.mode = mode
    app._config.hotkey.max_recording_s = cap
    return app, beeps


def _run_until(app, after_s=0.05):
    async def cycle():
        app._record_stop_event = asyncio.Event()
        asyncio.get_running_loop().call_later(after_s, app._record_stop_event.set)
        await app._transcription_cycle()

    asyncio.run(cycle())


@pytest.mark.parametrize("mode", ["hold", "toggle"])
def test_escape_cancels_the_recording_nothing_is_transcribed_or_pasted(monkeypatch, mode):
    app, _ = _record_app(monkeypatch, (None, True, None), mode=mode)
    _run_until(app)
    app._transcriber.finish.assert_not_awaited()
    app._audio.stop.assert_called_once()
    app._db.save_transcription.assert_not_called()
    app._transcriber.disconnect.assert_awaited()


def test_escape_before_the_recording_started_skips_the_cycle_without_a_marker_action(monkeypatch):
    app = _app(command=False, paste_last=True)
    app._stop_info = (None, True, None)
    app._paste_last_text = AsyncMock()
    _run_skipped_cycle(app)
    app._paste_last_text.assert_not_awaited()


def test_the_cap_ends_the_recording_and_the_text_so_far_is_processed(monkeypatch):
    pasted = []
    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: pasted.append(text))
    app._audio.start.side_effect = None
    app._stop_info = None
    app._config.hotkey.max_recording_s = 0.05  # nobody ends this recording
    app._mic_guard = MagicMock()

    async def cycle():
        app._record_stop_event = asyncio.Event()
        await asyncio.wait_for(app._transcription_cycle(), 5)

    asyncio.run(cycle())
    assert pasted == ["Hallo Welt"]
    app._hotkey.cancel.assert_called()  # the toggle intent is cleared too
    app._mic_guard.release_async.assert_called()


def test_a_toggle_take_under_300_ms_is_dropped(monkeypatch):
    app, _ = _record_app(monkeypatch, (None, False, None), mode="toggle")
    _run_until(app, after_s=0.01)
    app._transcriber.finish.assert_not_awaited()
    app._db.save_transcription.assert_not_called()


def test_a_marked_toggle_take_is_not_dropped_for_being_short(monkeypatch):
    app, _ = _record_app(monkeypatch, (PASTE_LAST, False, None), mode="toggle")
    app._paste_last_text = AsyncMock()
    _run_until(app, after_s=0.01)
    app._paste_last_text.assert_awaited_once()


def test_stop_marks_are_read_per_take_not_from_the_shared_hotkey_state(monkeypatch):
    # The hotkey already shows the next take's reset mark (None); this take's stop said paste-last.
    app, _ = _record_app(monkeypatch, (PASTE_LAST, False, None))
    app._hotkey.mark = None
    app._paste_last_text = AsyncMock()
    _run_until(app)
    app._paste_last_text.assert_awaited_once()


def test_paste_last_works_while_the_server_is_unreachable():
    app = _app(command=False, connect_error=RuntimeError("server down"), paste_last=True)
    app._paste_last_text = AsyncMock()
    _run_skipped_cycle(app)
    app._paste_last_text.assert_awaited_once()


def test_default_polish_works_while_the_server_is_unreachable():
    app = _app(command=False, connect_error=RuntimeError("server down"), polish=True)
    app._polish_selection = AsyncMock()
    _run_skipped_cycle(app)
    app._polish_selection.assert_awaited_once_with("", None)


def test_a_plain_dictation_with_the_server_down_gives_the_error_cue(monkeypatch):
    import nexusvox.app as app_module

    beeps = []
    monkeypatch.setattr(app_module, "beep_error", lambda: beeps.append("error"))
    monkeypatch.setattr(app_module, "_play", lambda beep: beep())
    app = _app(command=False, connect_error=RuntimeError("server down"))
    _run_skipped_cycle(app)
    assert beeps == ["error"]


def test_a_hotkey_press_during_a_phone_recording_gives_the_error_cue(monkeypatch):
    import nexusvox.app as app_module

    beeps = []
    monkeypatch.setattr(app_module, "_play", lambda beep: beeps.append(beep))
    app = _app(command=False)
    app._remote_recording = True
    app._on_hotkey_activate()
    assert beeps == [app_module.beep_error]
    app._hotkey.cancel.assert_called_once()


def test_a_short_toggle_take_is_measured_to_the_stop_chord_not_the_grace_window(monkeypatch):
    # Default binding: the take ends 50 ms after it started, the grace window delays the callback by 300 ms.
    app, _ = _record_app(monkeypatch, (None, False, None), mode="toggle")
    app._audio.start.side_effect = lambda: setattr(app, "_stop_info", (None, False, time.monotonic() + 0.05))
    _run_until(app, after_s=0.4)
    app._transcriber.finish.assert_not_awaited()


def test_the_cap_does_not_apply_to_a_phone_recording():
    app = _app(command=False)
    app._config.hotkey.max_recording_s = 0.01
    app._remote_audio = MagicMock()
    app._stream_audio = AsyncMock(return_value=bytearray())
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="", confidence=None))
    app._remote_cancelled = False
    app._remote_text = ""

    async def cycle():
        app._record_stop_event = asyncio.Event()
        asyncio.get_running_loop().call_later(0.2, app._record_stop_event.set)
        await app._transcription_cycle(remote=True)

    asyncio.run(cycle())
    app._remote_audio.stop.assert_called_once()
    app._hotkey.cancel.assert_not_called()  # a timeout would have cleared the hotkey intent


def test_default_polish_tap_works_when_the_http_backend_fails_only_at_finish(monkeypatch):
    app, _ = _record_app(monkeypatch, (POLISH, False, 100.2), mode="hold")
    app._press_time = 100.0
    app._transcriber.finish = AsyncMock(side_effect=RuntimeError("connection refused"))
    app._polish_selection = AsyncMock()
    _run_until(app, after_s=0.01)
    app._polish_selection.assert_awaited_once_with("", None)


def test_polish_with_a_spoken_instruction_is_not_replaced_by_the_default_when_the_server_fails(monkeypatch):
    app, _ = _record_app(monkeypatch, (POLISH, False, 105.0), mode="hold")
    app._press_time = 100.0
    app._transcriber.finish = AsyncMock(side_effect=RuntimeError("connection refused"))
    app._polish_selection = AsyncMock()
    _run_until(app, after_s=0.01)
    app._polish_selection.assert_not_awaited()


def test_offline_hold_polish_held_longer_than_a_tap_leaves_the_selection_alone():
    app = _app(command=False, connect_error=RuntimeError("server down"), polish=True)
    app._press_time = time.monotonic() - 5
    app._stop_info = (POLISH, False, time.monotonic())
    app._polish_selection = AsyncMock()
    _run_skipped_cycle(app)
    app._polish_selection.assert_not_awaited()


def test_offline_hold_gives_the_error_cue_at_once_not_after_the_release(monkeypatch):
    import nexusvox.app as app_module

    beeps = []
    monkeypatch.setattr(app_module, "_play", lambda beep: beeps.append(beep))
    app = _app(command=False, connect_error=RuntimeError("server down"))
    app._hotkey.active = True  # still held
    cue_seen_while_waiting = []

    async def cycle():
        app._record_stop_event = asyncio.Event()

        async def release():
            await asyncio.sleep(0.05)
            cue_seen_while_waiting.append(list(beeps))
            app._record_stop_event.set()

        asyncio.get_running_loop().create_task(release())
        await app._transcription_cycle()

    asyncio.run(cycle())
    assert cue_seen_while_waiting == [[app_module.beep_error]]


# -- hold mode: the start cue waits, an immediate discard stays silent -----------------


def _cue_app(monkeypatch, *, cancelled: bool, hold_s: float, mode: str = "hold"):
    """A hold cycle whose recording ends `hold_s` after it starts; returns the app and the cues it showed."""
    app, _ = _dictation_app(monkeypatch, lambda text, delay, enter: 0.0)
    cues: list[tuple] = []
    app._cue = lambda beep, state: cues.append((state, beep))
    app._hotkey.active = True
    app._hotkey.mode = mode
    app._stop_info = None

    def start():
        def stop():
            app._stop_info = (None, cancelled, None)
            app._record_stop_event.set()

        asyncio.get_running_loop().call_later(hold_s, stop)

    app._audio.start.side_effect = start
    return app, cues


def test_hold_start_cue_is_silent_when_the_recording_is_discarded_at_once(monkeypatch):
    app, cues = _cue_app(monkeypatch, cancelled=True, hold_s=0.01)
    _run_cycle(app)
    assert [state for state, _ in cues if state == "recording"] == []
    app._transcriber.finish.assert_not_called()  # nothing transcribed
    app._audio.start.assert_called_once()  # audio was captured from the first moment


def test_hold_start_cue_comes_after_the_delay(monkeypatch):
    import nexusvox.app as app_module

    monkeypatch.setattr(app_module, "START_CUE_DELAY_S", 0.02)
    app, cues = _cue_app(monkeypatch, cancelled=False, hold_s=0.2)
    _run_cycle(app)
    assert [state for state, _ in cues].count("recording") == 1


def test_hold_tap_shorter_than_the_delay_still_gets_its_start_cue(monkeypatch):
    """A normal release before the delay: the start cue is shown right before the stop cue, as a pair."""
    app, cues = _cue_app(monkeypatch, cancelled=False, hold_s=0.01)
    _run_cycle(app)
    states = [state for state, _ in cues]
    assert states.index("recording") < states.index("processing")
    assert states.count("recording") == 1


def test_hold_discard_leaves_the_tray_untouched(monkeypatch):
    app, _ = _cue_app(monkeypatch, cancelled=True, hold_s=0.01)
    _run_cycle(app)
    assert all(call.args != (True,) for call in app._tray.set_active.call_args_list)


def test_toggle_start_cue_is_silent_when_the_recording_is_discarded_at_once(monkeypatch):
    app, cues = _cue_app(monkeypatch, cancelled=True, hold_s=0.01, mode="toggle")
    _run_cycle(app)
    assert [state for state, _ in cues if state == "recording"] == []
    app._transcriber.finish.assert_not_called()
    assert all(call.args != (True,) for call in app._tray.set_active.call_args_list)


def test_toggle_start_cue_comes_after_the_delay(monkeypatch):
    import nexusvox.app as app_module

    monkeypatch.setattr(app_module, "START_CUE_DELAY_S", 0.02)
    app, cues = _cue_app(monkeypatch, cancelled=False, hold_s=0.2, mode="toggle")
    _run_cycle(app)
    assert [state for state, _ in cues].count("recording") == 1


def _watcher_calls(app):
    return [c[0] for c in app._edit_watcher.method_calls]


def test_polish_that_pasted_pauses_the_edit_watch_before_the_paste_and_ends_it_after(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Sehr geehrte Damen und Herren")
    order = []
    app._edit_watcher.pause.side_effect = lambda: order.append(("pause", len(calls["pasted"])))
    app._edit_watcher.cancel.side_effect = lambda: order.append(("cancel", len(calls["pasted"])))
    asyncio.run(app._polish_selection("", MagicMock()))
    assert order == [("pause", 0), ("cancel", 1)]


def test_polish_with_the_model_down_leaves_the_edit_watch_alone(monkeypatch):
    from nexusvox.llm import LlmError

    app, _ = _polish_app(monkeypatch, "hi leute", LlmError("refused"))
    asyncio.run(app._polish_selection("", MagicMock()))
    assert _watcher_calls(app) == []


def test_polish_with_the_focus_moved_leaves_the_edit_watch_alone(monkeypatch):
    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo Leute", windows=(1, 2))
    asyncio.run(app._polish_selection("", MagicMock()))
    assert calls["pasted"] == [] and _watcher_calls(app) == []


def test_polish_without_a_selection_leaves_the_edit_watch_alone(monkeypatch):
    app, _ = _polish_app(monkeypatch, "", "unused")
    asyncio.run(app._polish_selection("", MagicMock()))
    assert _watcher_calls(app) == []


def test_polish_whose_clipboard_failed_resumes_the_edit_watch(monkeypatch):
    import nexusvox.app as app_module
    from nexusvox.clipboard import ClipboardError

    app, _ = _polish_app(monkeypatch, "hi leute", "Hallo Leute")

    def broken(text, delay):
        raise ClipboardError("busy")

    monkeypatch.setattr(app_module, "inject_text", broken)
    asyncio.run(app._polish_selection("", MagicMock()))
    assert _watcher_calls(app) == ["pause", "resume"]


@pytest.mark.parametrize("reply", ["hi leute", ""])
def test_a_reply_that_changes_nothing_leaves_the_edit_watch_alone(monkeypatch, reply):
    app, _ = _polish_app(monkeypatch, "hi leute", reply)
    asyncio.run(app._polish_selection("", MagicMock()))
    assert _watcher_calls(app) == []


def test_an_extension_rewrite_ends_the_edit_watch_too(monkeypatch):
    import nexusvox.app as app_module

    app, calls = _polish_app(monkeypatch, "hi leute", "unused")
    registry = MagicMock(extensions=[object()])
    registry.selection_command.return_value = "Hallo Leute"
    monkeypatch.setattr(app_module, "loaded_extensions", lambda: registry)
    asyncio.run(app._polish_selection("shout", MagicMock()))
    assert calls["pasted"] == ["Hallo Leute"] and _watcher_calls(app) == ["pause", "cancel"]


def test_the_paste_key_ending_a_polish_reply_ends_the_edit_watch(monkeypatch):
    import nexusvox.app as app_module

    app, calls = _polish_app(monkeypatch, "hi leute", "Hallo Leute", windows=(1, 2))  # the paste failed
    asyncio.run(app._polish_selection("", MagicMock()))
    app._edit_watcher.reset_mock()
    asyncio.run(app._paste_last_text())
    assert calls["pasted"] == ["Hallo Leute"] and _watcher_calls(app) == ["pause", "cancel"]
    app._last_typed_rewritten = False  # a dictation is pasted again: the watch is not touched
    app._edit_watcher.reset_mock()
    asyncio.run(app._paste_last_text())
    assert _watcher_calls(app) == []
    assert app_module  # keeps the import used
