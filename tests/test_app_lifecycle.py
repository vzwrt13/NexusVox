"""The app's run loop and what can go wrong around a take: Quit, a model switch, stale stops, press times."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from app_parts import patch_app

from nexusvox.config import MODEL_REGISTRY, Config

try:
    from nexusvox.app.core import NexusVoxApp
    from nexusvox.dictation.hotkey import COMMAND, POLISH
except Exception:  # Windows-only imports (pynput hook, tray, SendInput) on other platforms
    pytest.skip("nexusvox.app.core needs the Windows runtime", allow_module_level=True)


@pytest.fixture(autouse=True)
def _silent_cues(monkeypatch):
    """No real chimes (CI has no audio device)."""
    patch_app(monkeypatch, "_play", lambda beep: None)


# -- Quit -----------------------------------------------------------------------------------------


def _running_app(monkeypatch) -> tuple[NexusVoxApp, MagicMock]:
    """An app `run()` can start, without any real component; also the dashboard API its shutdown reports to."""
    api = MagicMock()
    patch_app(monkeypatch, "running_dashboard_api", lambda: api)
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._autostart = True  # the dashboard is not opened: no browser in a test
    app._loop = None
    app._running = False
    app._hotkey = MagicMock()
    app._meeting_hotkey = MagicMock()
    app._tray = MagicMock()
    app._audio = MagicMock()
    app._edit_watcher = MagicMock()
    app._stats_pool = MagicMock()
    app._mic_guard = None
    app._remote_mic = None
    app._remote_recording = False
    app._transcriber_lock = asyncio.Lock()
    app._transcriber = MagicMock(needs_docker=True, warms_up_at_start=False, disconnect=AsyncMock())
    return app, api


def _held_lock() -> asyncio.Lock:
    """A transcriber lock that a dictation (or a model switch) holds."""
    lock = asyncio.Lock()
    asyncio.run(lock.acquire())
    return lock


def _quit_once(app: NexusVoxApp, ready: threading.Event) -> threading.Thread:
    """Press Quit from another thread, as the tray does, once *ready* is set."""

    def press() -> None:
        assert ready.wait(5)
        time.sleep(0.02)  # let the main task park on what it waits for
        app._on_quit()

    thread = threading.Thread(target=press, daemon=True)
    thread.start()
    return thread


def _assert_shut_down(app: NexusVoxApp, api: MagicMock) -> None:
    api.shutdown_meeting.assert_called_once()  # closes an open meeting, so its WAV files get proper headers
    app._hotkey.stop.assert_called_once()
    app._stats_pool.shutdown.assert_called_once_with(wait=True)
    app._transcriber.disconnect.assert_awaited()


def test_quit_while_idle_runs_the_shutdown_and_raises_nothing(monkeypatch):
    app, api = _running_app(monkeypatch)
    ready = threading.Event()
    app._tray.start.side_effect = ready.set
    thread = _quit_once(app, ready)
    app.run()
    thread.join(5)
    _assert_shut_down(app, api)


def _start_and_quit(monkeypatch, *, autostart: bool, setup_done: bool) -> MagicMock:
    """Run the app to its first idle moment and return the mock that stands in for opening the dashboard."""
    app, _ = _running_app(monkeypatch)
    app._autostart = autostart
    app._config = Config(setup_done=setup_done)
    opened = MagicMock()
    app._open_dashboard = opened
    ready = threading.Event()
    app._tray.start.side_effect = ready.set
    thread = _quit_once(app, ready)
    app.run()
    thread.join(5)
    return opened


def test_a_manual_start_opens_the_dashboard(monkeypatch):
    _start_and_quit(monkeypatch, autostart=False, setup_done=True).assert_called_once()


def test_the_autostart_stays_in_the_tray(monkeypatch):
    _start_and_quit(monkeypatch, autostart=True, setup_done=True).assert_not_called()


def test_the_autostart_still_opens_the_first_run_setup(monkeypatch):
    _start_and_quit(monkeypatch, autostart=True, setup_done=False).assert_called_once()


def test_only_the_autostart_marker_means_autostart():
    from nexusvox.__main__ import is_autostart

    assert is_autostart(["--autostart"]) and not is_autostart([])
    assert not is_autostart(["--other"])


def test_the_installer_registers_the_marker_and_a_desktop_shortcut():
    from pathlib import Path

    iss = (Path(__file__).parent.parent / "packaging" / "nexusvox.iss").read_text(encoding="utf-8")
    run_line = next(line for line in iss.splitlines() if "ValueType: string" in line and r"CurrentVersion\Run" in line)
    assert '--autostart"' in run_line
    assert "{autodesktop}" in iss and "{autoprograms}" in iss


def test_quit_during_a_recording_ends_it_at_once_and_runs_the_shutdown(monkeypatch):
    # Toggle mode: the keys are up, so nothing else would end this recording before the cap.
    app, api = _running_app(monkeypatch)
    ready = threading.Event()
    ended = []

    async def cycle(remote=False):
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            ended.append(app._transcriber_lock.locked())

    app._transcription_cycle = cycle
    app._tray.start.side_effect = lambda: app._loop.call_soon(app._record_start_event.set)
    thread = _quit_once(app, ready)
    app.run()
    thread.join(5)
    assert ended == [True] and not app._transcriber_lock.locked()
    _assert_shut_down(app, api)


def test_quit_during_a_phone_recording_still_answers_the_phone(monkeypatch):
    app, api = _running_app(monkeypatch)
    ready = threading.Event()
    app._remote_mic = MagicMock(close=AsyncMock())
    app._remote_recording = True
    app._remote_text = ""

    async def cycle(remote=False):
        ready.set()
        await asyncio.Event().wait()

    app._transcription_cycle = cycle
    app._tray.start.side_effect = lambda: app._loop.call_soon(app._record_start_event.set)
    thread = _quit_once(app, ready)
    app.run()
    thread.join(5)
    app._remote_mic.finished.assert_called_once_with("")
    _assert_shut_down(app, api)


def test_a_crash_of_the_main_loop_still_runs_the_shutdown(monkeypatch):
    app, api = _running_app(monkeypatch)
    app._serve = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        app.run()
    _assert_shut_down(app, api)


def test_the_shutdown_stops_the_hook_then_releases_the_mic_guard_and_survives_a_failing_step(monkeypatch):
    app, api = _running_app(monkeypatch)
    steps = []
    app._mic_guard = MagicMock()
    app._mic_guard.release.side_effect = lambda: steps.append("mic guard")

    def broken_hotkey_stop():
        steps.append("hotkey")
        raise RuntimeError("hook already gone")

    app._hotkey.stop.side_effect = broken_hotkey_stop
    loop_thread = []
    app._stats_pool.shutdown.side_effect = lambda **_kw: loop_thread.append(threading.get_ident())

    async def scenario():
        loop_thread.insert(0, threading.get_ident())
        await app._shutdown()

    asyncio.run(scenario())
    # No press can mute again once the hook is gone; the last step releases a hold it queued before.
    assert steps == ["hotkey", "mic guard", "mic guard"]
    _assert_shut_down(app, api)  # every later step still ran
    assert loop_thread[0] != loop_thread[1]  # a locked database never holds the loop


def test_a_second_cancel_during_the_shutdown_skips_no_step(monkeypatch):
    app, api = _running_app(monkeypatch)
    app._remote_mic = MagicMock(close=AsyncMock(side_effect=asyncio.CancelledError))  # Quit pressed again
    asyncio.run(app._shutdown())
    app._transcriber.disconnect.assert_awaited()
    _assert_shut_down(app, api)


def test_quit_cancels_the_main_task_only_once():
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._running = True
    app._loop = MagicMock()
    app._main_task = MagicMock()
    app._on_quit()
    app._on_quit()  # the tray and the dashboard both quit
    app._loop.call_soon_threadsafe.assert_called_once_with(app._main_task.cancel)


def test_a_background_task_is_kept_until_it_ends():
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._tasks = set()

    async def scenario():
        release = asyncio.Event()
        task = app._spawn(release.wait())
        held = task in app._tasks
        release.set()
        await task
        return held

    assert asyncio.run(scenario()) is True
    assert app._tasks == set()


# -- Model switch ---------------------------------------------------------------------------------


def _switch_app(monkeypatch, *, cycle_running: bool = False) -> NexusVoxApp:
    patch_app(monkeypatch, "save_config", lambda cfg: None)
    patch_app(monkeypatch, "create_transcriber", lambda *a, **k: MagicMock(needs_docker=False, disconnect=AsyncMock()))
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._device = "cpu"
    app._transcriber_lock = _held_lock() if cycle_running else asyncio.Lock()
    app._switch_status = "idle"
    app._switch_error = None
    app._switch_reset = None
    app._switch_note = None
    app._transcriber = MagicMock(needs_docker=False, disconnect=AsyncMock())
    app._warms_up = lambda: False
    return app


def test_a_model_switch_during_a_dictation_is_refused_and_the_transcriber_stays(monkeypatch):
    app = _switch_app(monkeypatch, cycle_running=True)
    live = app._transcriber
    with pytest.raises(RuntimeError, match="during a dictation"):
        asyncio.run(app.switch_model("whisper-large-v3-turbo"))
    assert app._transcriber is live
    live.disconnect.assert_not_awaited()
    # The dashboard only polls the status: the refusal has to be readable there.
    assert app._switch_status == "error" and "dictation" in app._switch_error


def test_a_refused_switch_does_not_disturb_one_already_running(monkeypatch):
    app = _switch_app(monkeypatch, cycle_running=True)
    app._switch_status = "starting"
    with pytest.raises(RuntimeError, match="already in progress"):
        asyncio.run(app.switch_model("whisper-large-v3-turbo"))
    assert app._switch_status == "starting"


def test_choosing_the_active_model_during_a_dictation_is_no_error(monkeypatch):
    app = _switch_app(monkeypatch, cycle_running=True)
    asyncio.run(app.switch_model(app._config.inference.model))
    assert app._switch_status == "idle"


def _fast_status_reset(monkeypatch) -> None:
    """The 5 s that a switch status stays readable pass at once."""
    real_sleep = asyncio.sleep

    async def fast(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast)


def test_a_refused_switch_clears_its_error_like_a_finished_one(monkeypatch):
    app = _switch_app(monkeypatch, cycle_running=True)
    _fast_status_reset(monkeypatch)

    async def scenario():
        with pytest.raises(RuntimeError, match="during a dictation"):
            await app.switch_model("whisper-large-v3-turbo")
        refused = app._switch_status
        await app._switch_reset
        return refused

    assert asyncio.run(scenario()) == "error"
    assert app._switch_status == "idle" and app._switch_error is None


def test_a_failed_switch_clears_its_error_like_a_finished_one(monkeypatch):
    app, _docker, _built = _cuda_switch_app(monkeypatch, start_error="port is already allocated")
    _fast_status_reset(monkeypatch)

    async def scenario():
        await app.switch_model("parakeet-tdt-0.6b")
        failed = app._switch_status
        await app._switch_reset
        return failed

    assert asyncio.run(scenario()) == "error"
    assert app._switch_status == "idle" and app._switch_error is None


def test_an_interrupted_switch_settles_its_status_and_frees_the_transcriber(monkeypatch):
    app = _switch_app(monkeypatch)
    app._transcriber.disconnect = AsyncMock(side_effect=asyncio.Event().wait)  # stuck in "stopping"

    async def scenario():
        switch = asyncio.create_task(app.switch_model("whisper-large-v3-turbo"))
        await asyncio.sleep(0.01)
        held = app._transcriber_lock.locked()
        switch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await switch
        return held

    assert asyncio.run(scenario()) is True
    assert app._switch_status == "error" and "interrupted" in app._switch_error
    assert not app._transcriber_lock.locked()  # dictation works again


def test_a_switch_stands_once_its_config_is_saved(monkeypatch):
    app, _docker, _built = _cuda_switch_app(monkeypatch)
    saved = []
    patch_app(monkeypatch, "save_config", lambda cfg: saved.append(cfg.inference.model))

    def warm_up_check_fails():
        raise KeyError("registry")

    app._warms_up = warm_up_check_fails
    with pytest.raises(KeyError):
        asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    # The file names the new model, and so does the app: the next start comes up on what the dashboard showed.
    assert saved == ["parakeet-tdt-0.6b"]
    assert app._config.inference.model == "parakeet-tdt-0.6b" and app._switch_status == "ready"


def test_a_failed_switch_keeps_the_transcriber_it_never_replaced(monkeypatch):
    app, _docker, built = _cuda_switch_app(monkeypatch, device="cuda", healthy=False)
    old = app._transcriber
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert app._transcriber is old and built == []


def test_a_switch_that_fails_after_the_swap_rebuilds_the_old_model_and_warms_it_up(monkeypatch):
    app, _docker, built = _cuda_switch_app(monkeypatch)

    def save_fails(_config):
        raise OSError("config.toml is read-only")

    patch_app(monkeypatch, "save_config", save_fails)
    app._warms_up = lambda: True
    app._tasks = set()
    app._preload_transcriber = AsyncMock()

    async def scenario():
        await app.switch_model("parakeet-tdt-0.6b")
        await asyncio.gather(*app._tasks)

    asyncio.run(scenario())
    assert app._switch_status == "error" and app._config.inference.model == "whisper-large-v3-turbo"
    assert len(built) == 2  # the new model, then the old one again
    assert built[-1][0][0].model == "whisper-large-v3-turbo"
    app._preload_transcriber.assert_awaited_once()


def test_the_timer_of_a_finished_switch_leaves_a_newer_switch_alone(monkeypatch):
    real_sleep = asyncio.sleep
    patch_app(monkeypatch, "docker_ctl", MagicMock())
    app = _switch_app(monkeypatch)

    async def fast_reset(_seconds):
        await real_sleep(0.01)

    async def scenario():
        monkeypatch.setattr(asyncio, "sleep", fast_reset)
        await app.switch_model("whisper-large-v3-turbo")
        assert app._switch_status == "ready"
        first_timer = app._switch_reset
        release = asyncio.Event()
        slow = AsyncMock(side_effect=release.wait)  # the second switch waits here, in "stopping"
        app._transcriber.disconnect = slow
        second = asyncio.create_task(app.switch_model("parakeet-tdt-0.6b"))
        await real_sleep(0.05)  # well past the first timer
        status = app._switch_status
        release.set()
        await second
        return status, first_timer

    status, first_timer = asyncio.run(scenario())
    assert status == "stopping"
    assert first_timer.cancelled()


# -- A take -----------------------------------------------------------------------------------------


def _take_app(monkeypatch, *, stop_info, press_time=100.0) -> NexusVoxApp:
    """An app whose cycle records until the test sets the stop event; nothing real behind it."""
    patch_app(monkeypatch, "running_dashboard_api", lambda: None)
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._transcriber = MagicMock(connect=AsyncMock(), disconnect=AsyncMock())
    app._hotkey = MagicMock(command=False, mark=None, active=False, mode="hold")
    app._record_start_event = asyncio.Event()
    app._stop_info = stop_info
    app._config = MagicMock(hotkey=MagicMock(max_recording_s=600))
    app._overlay = None
    app._press_time = press_time
    app._tray = MagicMock()
    app._mic_guard = None
    app._audio = MagicMock()
    app._stream_audio = AsyncMock(return_value=bytearray(b"\x01\x00"))
    app._polish_selection = AsyncMock()
    return app


def test_a_stop_from_an_earlier_tap_does_not_skip_the_held_press(monkeypatch):
    # Hold, during a slow transcription: press, release, press and keep holding. The two starts collapsed.
    app = _take_app(monkeypatch, stop_info=(None, False, 1.0))
    app._hotkey.active = True
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="", confidence=None))
    recorded = []
    app._audio.start.side_effect = lambda: recorded.append(app._record_stop_event.is_set())

    async def scenario():
        app._record_stop_event = asyncio.Event()
        app._record_stop_event.set()  # from the first release
        app._record_start_event.set()  # from the second press, while the first one's cycle connected
        cycle = asyncio.create_task(app._transcription_cycle())
        await asyncio.sleep(0.02)
        assert not cycle.done()  # recording, and waiting for the real release
        app._stop_info = (None, False, 2.0)
        app._hotkey.active = False
        app._record_stop_event.set()
        await cycle
        return app._record_stop_event.is_set(), app._record_start_event.is_set()

    # Nothing stale is left for the next take, and no cycle starts without a press.
    assert asyncio.run(scenario()) == (False, False)
    assert recorded == [False]
    assert app._stop_info is None


def test_a_polish_tap_after_a_dropped_stop_is_timed_from_its_own_press(monkeypatch):
    # The first press is at 100; it is released and pressed again at 104 while the cycle connects, and the
    # cycle resumes in the hook's gap: the hotkey is already active, its press callback has not run yet.
    app = _take_app(monkeypatch, stop_info=(None, False, 100.2))
    app._transcriber.finish = AsyncMock(side_effect=RuntimeError("connection refused"))

    async def second_press_half_done():
        app._hotkey.active = True
        app._record_stop_event.set()  # the first release

    def press_callback_finishes():  # what `settle()` waits for: `_on_hotkey_activate` on the hook thread
        app._press_time = 104.0
        asyncio.get_running_loop().call_soon_threadsafe(app._record_start_event.set)

    app._transcriber.connect = AsyncMock(side_effect=second_press_half_done)
    app._hotkey.settle.side_effect = press_callback_finishes

    def tap_released():
        app._stop_info = (POLISH, False, 104.2)  # a quick polish tap
        app._hotkey.active = False
        app._record_stop_event.set()

    app._audio.start.side_effect = lambda: asyncio.get_running_loop().call_later(0.01, tap_released)

    async def scenario():
        app._record_stop_event = asyncio.Event()
        await app._transcription_cycle()

    asyncio.run(scenario())
    app._polish_selection.assert_awaited_once_with("", None)
    assert not app._record_start_event.is_set()  # the press was this take: no cycle of its own


def _switching_app(monkeypatch, *, stop_info=None) -> NexusVoxApp:
    """A take app while a model switch holds the transcriber."""
    app = _take_app(monkeypatch, stop_info=stop_info)
    app._transcriber_lock = _held_lock()
    app._offline_stop = AsyncMock()
    app._transcription_cycle = AsyncMock()
    return app


def test_no_take_runs_while_a_model_switch_holds_the_transcriber(monkeypatch):
    app = _switching_app(monkeypatch)

    async def scenario():
        app._record_stop_event = asyncio.Event()
        await app._run_cycle(remote=False)

    asyncio.run(scenario())
    app._offline_stop.assert_awaited_once_with(100.0)  # paste-last and a polish tap still work
    app._transcription_cycle.assert_not_awaited()
    app._transcriber.connect.assert_not_awaited()


def test_a_press_during_a_switch_drops_the_stale_stop_of_an_earlier_tap(monkeypatch):
    # Hold: a tap during the last cycle left its stop; the keys are down again while a switch runs.
    app = _switching_app(monkeypatch, stop_info=(POLISH, False, 101.0))
    app._hotkey.active = True
    app._press_time = 103.0

    async def scenario():
        app._record_stop_event = asyncio.Event()
        app._record_stop_event.set()
        await app._run_cycle(remote=False)
        return app._record_stop_event.is_set()

    assert asyncio.run(scenario()) is False  # the held press's own release ends it, not the old stop
    assert app._stop_info is None
    app._offline_stop.assert_awaited_once_with(103.0)


def test_a_phone_recording_during_a_switch_gets_no_text_and_no_traceback(monkeypatch, caplog):
    app = _switching_app(monkeypatch)
    app._remote_audio = MagicMock()
    app._remote_text = "old"
    with caplog.at_level("INFO", logger="nexusvox.app"):
        asyncio.run(app._run_cycle(remote=True))
    assert app._remote_text == ""
    app._remote_audio.reset.assert_called_once()
    assert not [r for r in caplog.records if r.exc_info]


def test_a_stop_with_the_hotkey_up_still_skips_the_cycle(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(None, False, 1.0))

    async def scenario():
        app._record_stop_event = asyncio.Event()
        app._record_stop_event.set()
        await app._transcription_cycle()

    asyncio.run(scenario())
    app._audio.start.assert_not_called()


def test_a_long_polish_is_no_tap_when_the_next_press_came_during_finish(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(POLISH, False, 105.0))  # held for 5 s

    async def next_press_then_fail():
        app._press_time = 104.9  # the hotkey went down again while the server worked
        raise RuntimeError("connection refused")

    app._transcriber.finish = AsyncMock(side_effect=next_press_then_fail)

    async def scenario():
        app._record_stop_event = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, app._record_stop_event.set)
        await app._transcription_cycle()

    asyncio.run(scenario())
    app._polish_selection.assert_not_awaited()  # the instruction is lost: the selection stays as it was


def test_offline_a_long_polish_is_no_tap_when_the_next_press_came_meanwhile(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(POLISH, False, 105.0))
    app._hotkey.mark = POLISH
    app._send_os_command = MagicMock()
    app._press_time = 104.9  # already the next press

    async def scenario():
        app._record_stop_event = asyncio.Event()
        app._record_stop_event.set()
        await app._offline_stop(100.0)

    asyncio.run(scenario())
    app._polish_selection.assert_not_awaited()


# -- Storing ----------------------------------------------------------------------------------------


def test_a_take_without_any_audio_stores_no_file(tmp_path):
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._config.database.path = str(tmp_path / "db.sqlite")
    app._db = MagicMock()
    app._store_audio(bytearray(), 7)  # stopped inside the first audio block
    app._db.update_audio_path.assert_not_called()
    assert not (tmp_path / app._config.database.audio_dir).exists()


def test_a_polish_tap_without_any_audio_saves_its_row(tmp_path, monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "und")
    from nexusvox.app.records import _Recording

    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._config.database.path = str(tmp_path / "db.sqlite")
    app._db = MagicMock()
    app._transcriber = MagicMock(model="m")
    app._save_command(_Recording(bytearray(), 10, None, 1), "[polish] default", "")
    app._db.save_transcription.assert_called_once()
    app._db.update_audio_path.assert_not_called()


def _off_the_loop(app: NexusVoxApp, seen: list) -> None:
    """Record, for each save, whether it ran on the event loop's thread."""
    loop_thread = threading.get_ident()

    def note(*_args, **_kwargs):
        seen.append(threading.get_ident() != loop_thread)
        return MagicMock(id=1)

    app._save_command = MagicMock(side_effect=note)
    app._store_audio = MagicMock(side_effect=note)
    app._db = MagicMock()
    app._db.save_transcription.side_effect = note


def test_a_spoken_command_is_saved_off_the_event_loop(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(None, False, None))
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="nexus assistant hi", confidence=None))
    app._run_command = AsyncMock(return_value="[assistant] hi")
    app._config.experimental.flag_command = False
    app._audio.start.side_effect = lambda: app._record_stop_event.set()
    seen = []

    async def scenario():
        app._record_stop_event = asyncio.Event()
        _off_the_loop(app, seen)
        await app._transcription_cycle()

    asyncio.run(scenario())
    assert seen == [True]


def test_an_os_control_command_is_saved_off_the_event_loop(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(COMMAND, False, None))
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="open the browser", confidence=None))
    app._send_os_command = MagicMock(return_value="ok")
    app._audio.start.side_effect = lambda: app._record_stop_event.set()
    seen = []

    async def scenario():
        app._record_stop_event = asyncio.Event()
        _off_the_loop(app, seen)
        await app._transcription_cycle()

    asyncio.run(scenario())
    assert seen == [True]


# -- Empty recordings ------------------------------------------------------------------------------


def _run_take(app: NexusVoxApp) -> None:
    app._audio.start.side_effect = lambda: app._record_stop_event.set()

    async def scenario():
        app._record_stop_event = asyncio.Event()
        await app._transcription_cycle()

    asyncio.run(scenario())


def test_a_recording_without_audio_frames_sends_nothing(monkeypatch, caplog):
    app = _take_app(monkeypatch, stop_info=(None, False, None))
    app._stream_audio = AsyncMock(return_value=bytearray())
    app._transcriber.finish = AsyncMock()
    with caplog.at_level("INFO", logger="nexusvox.app"):
        _run_take(app)
    app._transcriber.finish.assert_not_awaited()
    app._transcriber.disconnect.assert_awaited()
    assert [r.getMessage() for r in caplog.records if "no audio frames" in r.getMessage()] == [
        "Recording has no audio frames, nothing to transcribe"
    ]
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "EXCEPTION")]


def test_a_polish_tap_without_audio_frames_still_polishes_with_the_default(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(POLISH, False, None))
    app._stream_audio = AsyncMock(return_value=bytearray())
    app._transcriber.finish = AsyncMock()
    _run_take(app)
    app._transcriber.finish.assert_not_awaited()
    app._polish_selection.assert_awaited_once_with("", None)


def test_a_short_recording_with_frames_is_still_sent(monkeypatch):
    app = _take_app(monkeypatch, stop_info=(None, False, None))
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="", confidence=None))
    _run_take(app)
    app._transcriber.finish.assert_awaited_once()


@pytest.mark.parametrize(
    ("stop_info", "text"),
    [
        ((None, False, None), ""),  # an empty transcription
        ((None, False, None), "hello"),  # a dictation that was typed
        ((POLISH, False, None), "shorter"),  # an instruction for the selection
        ((None, True, None), "unused"),  # dropped by Escape
    ],
)
def test_every_take_disconnects_its_transcriber_exactly_once(monkeypatch, stop_info, text):
    app = _dictation_app(monkeypatch, stop_info=stop_info, text=text)
    app._hide_os_control = AsyncMock()
    _run_take(app)
    app._transcriber.disconnect.assert_awaited_once()


# -- What a dictation saves ------------------------------------------------------------------------


def _dictation_app(monkeypatch, *, stop_info=(None, False, None), text="hello") -> NexusVoxApp:
    """A take that goes all the way to the paste and the saved row; nothing real behind it."""
    app = _take_app(monkeypatch, stop_info=stop_info)
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text=text, confidence=0.9))
    app._transcriber.model = "parakeet-tdt-0.6b"
    app._transcriber.device = "gpu"
    app._config.experimental.flag_command = False
    app._config.learn_from_edits.enabled = False
    app._run_command = AsyncMock(return_value=None)
    app._paste = MagicMock(return_value=(None, "notepad.exe"))
    app._db = MagicMock()
    app._db.save_transcription.return_value = MagicMock(id=1)
    app._stats_pool = MagicMock()
    app._store_audio = MagicMock()
    processed = MagicMock(typed=text, saved=text, press_enter=False, dictionary_hits={})
    patch_app(monkeypatch, "process_text", lambda *a, **k: processed)
    patch_app(monkeypatch, "stored_language", lambda text: "en")
    return app


def test_the_dictionary_is_read_off_the_event_loop(monkeypatch):
    app = _dictation_app(monkeypatch)
    threads = []
    app._db.get_dictionary_entries.side_effect = lambda: threads.append(threading.get_ident()) or []

    async def scenario():
        threads.append(threading.get_ident())
        app._audio.start.side_effect = lambda: app._record_stop_event.set()
        app._record_stop_event = asyncio.Event()
        await app._transcription_cycle()

    asyncio.run(scenario())
    loop_thread, read_thread = threads
    assert read_thread != loop_thread


def test_the_server_is_hung_up_before_the_polish_runs(monkeypatch):
    app = _dictation_app(monkeypatch, stop_info=(POLISH, False, None), text="shorter")
    seen = []
    app._polish_selection = AsyncMock(side_effect=lambda *a: seen.append(app._transcriber.disconnect.await_count))
    _run_take(app)
    assert seen == [1]  # an LLM that takes seconds never holds an idle server session
    app._transcriber.disconnect.assert_awaited_once()


def test_a_dictation_is_saved_with_the_model_that_transcribed_it(monkeypatch):
    app = _dictation_app(monkeypatch)
    after_switch = MagicMock(model="whisper-large-v3-turbo", device="cpu", disconnect=AsyncMock())

    def paste_while_the_model_changes(*_args):
        app._transcriber = after_switch
        return None, "notepad.exe"

    app._paste = MagicMock(side_effect=paste_while_the_model_changes)
    _run_take(app)
    saved = app._db.save_transcription.call_args.kwargs
    assert (saved["model"], saved["transcribe_device"]) == ("parakeet-tdt-0.6b", "gpu")


def test_a_command_row_is_saved_with_the_model_of_its_recording(monkeypatch):
    patch_app(monkeypatch, "stored_language", lambda text: "en")
    from nexusvox.app.records import _Recording

    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._db = MagicMock()
    app._transcriber = MagicMock(model="whisper-large-v3-turbo")  # switched since the recording
    app._store_audio = MagicMock()
    app._save_command(_Recording(bytearray(b"\x01\x00"), 10, None, 1, "gpu", "parakeet-tdt-0.6b"), "[x] y", "y")
    assert app._db.save_transcription.call_args.kwargs["model"] == "parakeet-tdt-0.6b"


def _cuda_switch_app(
    monkeypatch,
    *,
    device: str = "auto",
    frozen: bool = False,
    unavailable: str | None = None,
    start_error: str | None = None,
    healthy: bool = True,
    model: str = "whisper-large-v3-turbo",
):
    """An app on a GPU machine that switches to Parakeet; Docker is a mock and never started."""
    docker = MagicMock()
    docker.unavailable_reason.return_value = unavailable
    if unavailable:  # the real compose calls fail the same way while Docker is out of reach
        docker.stop_profile.side_effect = RuntimeError("Failed to stop profile")
        docker.start_profile.side_effect = RuntimeError("Failed to start profile parakeet")
    if start_error:
        docker.start_profile.side_effect = RuntimeError(start_error)
    docker.wait_for_healthy.return_value = healthy
    patch_app(monkeypatch, "docker_ctl", docker)
    patch_app(monkeypatch, "is_frozen", lambda: frozen)
    app = _switch_app(monkeypatch)
    built = []
    patch_app(monkeypatch, "create_transcriber", lambda *a, **k: built.append((a, k)) or MagicMock(needs_docker=False))
    app._device = "cuda"
    app._config.inference.device = device
    app._config.inference.model = model
    app._transcriber = MagicMock(needs_docker=True, disconnect=AsyncMock())
    return app, docker, built


def test_docker_out_of_reach_falls_back_to_the_cpu_on_auto_even_with_a_docker_model_running(monkeypatch, caplog):
    app, docker, built = _cuda_switch_app(monkeypatch, unavailable="Docker is not running")
    with caplog.at_level("INFO"):
        asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert app._switch_status == "ready" and app._config.inference.model == "parakeet-tdt-0.6b"
    assert app.get_switch_status()["note"] == "Docker is not running, using the CPU"
    docker.stop_profile.assert_not_called()  # it would raise with Docker down
    docker.start_profile.assert_not_called()
    assert not [r for r in caplog.records if r.exc_info]  # an expected case, no traceback
    assert any("runs on the CPU" in r.getMessage() for r in caplog.records)


def test_the_cpu_fallback_keeps_the_auto_engine_for_parakeet_so_the_gpu_can_return(monkeypatch):
    app, _docker, built = _cuda_switch_app(monkeypatch, unavailable="Docker is not running")
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert built[-1][1] == {"device": "cuda", "auto_fallback": True}


def test_the_cpu_fallback_for_whisper_drops_a_gpu_compute_type(monkeypatch):
    app, _docker, built = _cuda_switch_app(monkeypatch, unavailable="Docker is not running", model="parakeet-tdt-0.6b")
    app._config.inference.compute_type = "float16"
    asyncio.run(app.switch_model("whisper-large-v3-turbo"))
    (config,), kwargs = built[-1]
    assert kwargs == {"device": "cpu"} and config.compute_type is None
    assert app._config.inference.compute_type == "float16"  # the user's setting is untouched


def test_a_frozen_build_switches_to_the_cpu_instead_of_raising(monkeypatch):
    app, docker, built = _cuda_switch_app(
        monkeypatch, frozen=True, unavailable="Docker is not available in the installed app"
    )
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert app._switch_status == "ready"
    assert built[-1][1] == {"device": "cpu"}  # no server in the installed app, no auto engine
    assert "installed app" in app.get_switch_status()["note"]
    docker.start_profile.assert_not_called()


def test_a_frozen_build_still_refuses_a_model_without_an_in_process_path(monkeypatch):
    app, _docker, _built = _cuda_switch_app(monkeypatch, frozen=True)
    monkeypatch.setitem(MODEL_REGISTRY, "needs-docker", {"docker_profile": "x", "default_url": "u", "health_url": "h"})
    with pytest.raises(RuntimeError, match="Docker"):
        asyncio.run(app.switch_model("needs-docker"))


def test_a_real_compose_failure_with_docker_running_stays_an_error(monkeypatch):
    app, _docker, built = _cuda_switch_app(monkeypatch, start_error="port is already allocated")
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert app._switch_status == "error" and "port is already allocated" in app._switch_error
    assert app._config.inference.model == "whisper-large-v3-turbo"
    assert "note" not in app.get_switch_status()


def test_a_container_that_never_gets_healthy_falls_back_to_the_cpu_on_auto(monkeypatch):
    app, docker, _built = _cuda_switch_app(monkeypatch, healthy=False)
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert app._switch_status == "ready"
    assert app.get_switch_status()["note"] == "The Docker container did not become healthy, using the CPU"
    docker.stop_profile.assert_called_with("parakeet")  # the half-started container is stopped


def test_explicit_cuda_keeps_the_error_and_does_not_fall_back(monkeypatch):
    app, docker, built = _cuda_switch_app(monkeypatch, device="cuda", start_error="Failed to start profile parakeet")
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    assert app._switch_status == "error" and "Failed to start profile parakeet" in app._switch_error
    assert app._config.inference.model == "whisper-large-v3-turbo"
    docker.unavailable_reason.assert_not_called()


def test_explicit_cuda_in_a_frozen_build_still_raises(monkeypatch):
    app, _docker, _built = _cuda_switch_app(monkeypatch, device="cuda", frozen=True)
    with pytest.raises(RuntimeError, match="Docker"):
        asyncio.run(app.switch_model("parakeet-tdt-0.6b"))


def test_the_note_survives_a_first_download_but_not_a_later_refusal(monkeypatch):
    app, _docker, _built = _cuda_switch_app(monkeypatch, unavailable="Docker is not running")
    asyncio.run(app.switch_model("parakeet-tdt-0.6b"))
    app._switch_status = "idle"  # the 5 s timer ran
    assert "note" not in app.get_switch_status()
    app._transcriber.load_status.return_value = {"state": "downloading", "error": None}
    assert app.get_switch_status()["note"] == "Docker is not running, using the CPU"
    app._transcriber_lock = _held_lock()
    with pytest.raises(RuntimeError, match="during a dictation"):
        asyncio.run(app.switch_model("whisper-large-v3-turbo"))
    assert "note" not in app.get_switch_status()
