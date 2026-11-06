"""What reaches the app from outside the cycle: hotkeys, the phone and the os-control service."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

from .app_common import QUICK_TAP_S, logger
from .app_feedback import _play
from .assistant import send_to_service
from .dashboard import ensure_dashboard_api, open_dashboard
from .feedback import beep_error, beep_start, beep_stop
from .hotkey import PASTE_LAST, POLISH
from .logsetup import Text
from .overlay import ERROR


class InputEvents:
    """The event part of `NexusVoxApp`: callbacks from the hook thread, the phone server and the offline path."""

    def _on_hotkey_activate(self) -> None:
        """Called from hotkey thread when push-to-talk starts."""
        if self._remote_recording:
            # A phone is recording. Drop this press's intent, so its release (or, in
            # toggle mode, the next press) does not queue a stale stop that would skip
            # the next dictation, and the phone is not refused as busy meanwhile.
            self._hotkey.cancel()
            _play(beep_error)  # otherwise the dead hotkey looks broken
            return
        self._press_time = time.monotonic()
        if self._mic_guard is not None and self._config.mic_guard.enabled:
            self._mic_guard.hold_async()
        if self._loop is not None and self._record_start_event is not None:
            self._loop.call_soon_threadsafe(self._record_start_event.set)

    def _on_hotkey_deactivate(self) -> None:
        """Called from hotkey thread when push-to-talk ends."""
        if self._remote_recording:
            return
        self._stop_info = (self._hotkey.mark, False, self._hotkey.stop_requested_at)
        if self._mic_guard is not None:
            self._mic_guard.release_async()
        if self._loop is not None and self._record_stop_event is not None:
            self._loop.call_soon_threadsafe(self._record_stop_event.set)

    def _on_hotkey_cancel(self) -> None:
        """Called from the hotkey thread when the recording is dropped, by Escape or (Hold) by a foreign key
        joining the chord: nothing is transcribed or pasted."""
        self._stop_info = (None, True, None)
        if self._mic_guard is not None:
            self._mic_guard.release_async()
        if self._loop is not None and self._record_stop_event is not None:
            self._loop.call_soon_threadsafe(self._record_stop_event.set)

    def _next_stop(self) -> tuple[str | None, bool, float | None]:
        """What the latest hotkey stop was: (marker, cancelled, time of the gesture). Read once per take."""
        info, self._stop_info = self._stop_info, None
        return info if info is not None else (None, False, None)

    async def _offline_stop(self, pressed_at: float) -> None:
        """The transcription server is unreachable: paste-last and a quick polish tap need none.

        The error cue goes out at once, so a Hold user is not left dictating into nothing. A recording that can
        still get a marker (Hold, or a marker already set) then waits for its end; a Toggle recording without a
        marker has no way to say what it wants and ends here. A polish held longer than a tap carried an
        instruction that is lost: the selection stays as it is. *pressed_at* is this take's key press: by the
        time the stop arrives, `_press_time` may already be the next one.
        """
        self._cue(beep_error, ERROR)
        event = self._record_stop_event
        waits = self._hotkey.active and (self._hotkey.mode == "hold" or self._hotkey.mark is not None)
        if not event.is_set() and not waits:
            self._hotkey.cancel()
            await self._hide_os_control()
            return
        if not event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), self._config.hotkey.max_recording_s or None)
        if event.is_set():
            event.clear()
            mark, cancelled, at = self._next_stop()
        else:
            mark, cancelled, at = self._hotkey.mark, False, None
            self._hotkey.cancel()
        held_s = (at or time.monotonic()) - pressed_at
        if cancelled:
            await self._hide_os_control()
        elif mark == PASTE_LAST:
            await self._paste_last_text()
        elif mark == POLISH and held_s < QUICK_TAP_S:
            await self._polish_selection("", None)
        else:
            await self._hide_os_control()

    def _on_meeting_hotkey(self) -> None:
        """Called inside the keyboard hook: the work goes to a thread, the hook never blocks."""
        threading.Thread(target=self._toggle_meeting, name="meeting-toggle", daemon=True).start()

    def _toggle_meeting(self) -> None:
        """Stop the running meeting, or start one. While the consent hint is on, a start opens the dashboard's
        Meetings tab instead, where the hint is shown and the recording is started by its button. A press that
        arrives while the previous one is still being handled (devices opening) is ignored."""
        if not self._meeting_toggle_lock.acquire(blocking=False):
            return
        try:
            api = ensure_dashboard_api(self._db, self._config, self)
            result = api.toggle_meeting(ask_consent=True)
            if result.get("action") == "consent":
                open_dashboard(self._db, self._config, app=self, tab="meetings")
                return
            # A stop that only found the transcriber busy still ended the recording: that is no error.
            if result.get("ok") or result.get("busy"):
                _play(beep_stop if result.get("action") == "stop" else beep_start)
            else:
                _play(beep_error)
        except Exception:
            logger.exception("Meeting hotkey failed")
            _play(beep_error)
        finally:
            self._meeting_toggle_lock.release()

    def _on_remote_start(self) -> bool:
        """Called on the loop when a phone starts a recording. False while busy: a cycle
        is running or queued, or the hotkey is held."""
        if self._cycle_running or self._hotkey.active or self._record_start_event.is_set():
            return False
        self._remote_recording = True
        self._remote_cancelled = False
        self._remote_text = ""
        self._record_stop_event.clear()
        self._record_start_event.set()
        return True

    def _on_remote_stop(self) -> None:
        """Called on the loop when the phone stops (or drops) its recording."""
        self._record_stop_event.set()

    def _on_remote_cancel(self) -> None:
        """Called on the loop when the phone drops its recording without wanting text."""
        self._remote_cancelled = True
        self._record_stop_event.set()

    def _on_hotkey_command(self) -> None:
        """Called from the hotkey thread when the command key marks this recording.
        Shows the window numbers now, while the user speaks; never blocks the hook."""
        threading.Thread(target=self._send_os_command, args=("::show",), daemon=True).start()

    async def _hide_os_control(self) -> None:
        """Hide the window numbers again when a command recording ends without a transcript.

        `::show` went out on the key press; a skipped or failed cycle never reaches the
        normal send, and the widget would otherwise stay on screen.
        """
        if not self._hotkey.command:
            return
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._send_os_command, "::hide")
        except Exception:
            logger.exception("Could not hide the os-control window numbers")

    def _send_os_command(self, text: str) -> str | None:
        """Send one line to the os-control service; None when nothing answers."""
        cfg = self._config.os_control
        try:
            reply = send_to_service(cfg.service, text, cfg.host, cfg.port, cfg.timeout_s)
        except OSError as exc:
            logger.warning("os-control not reachable (%s)", exc)
            return None
        logger.info("os-control command %r -> %s", Text(text), reply or "(no reply)")
        return reply
