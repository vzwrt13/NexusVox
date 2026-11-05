"""Main application orchestrator — wires hotkey, audio, transcriber, and injector."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import threading
import time
import wave
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import docker_ctl
from .assistant import parse_assistant_command, send_to_assistant, send_to_service
from .audio import AudioCapture
from .clipboard import ClipboardError
from .config import (
    DEFAULT_MODEL,
    FEEDBACK_MODES,
    MODEL_REGISTRY,
    Config,
    InferenceConfig,
    model_needs_docker,
    resolve_device,
    save_config,
)
from .dashboard import ensure_dashboard_api, open_dashboard, running_dashboard_api
from .db import Database
from .edit_watch import EditWatcher
from .extensions import HOOK_TIMEOUT_S, ActiveApp, load_extensions, loaded_extensions
from .feedback import beep_error, beep_flag, beep_start, beep_stop
from .hotkey import COMMAND, PASTE_LAST, POLISH, HotkeyListener, MeetingHotkey, describe_hotkey
from .injector import foreground_window, inject_text
from .lang_detect import stored_language
from .llm import LlmError
from .llm import warm_up as llm_warm_up
from .logsetup import Text
from .mic_guard import MicGuard, create_mic_guard
from .models import COMMAND_APP
from .os_commands import WINDOW_ACTIONS, NexusCommand, execute_nexus_command, parse_nexus_command
from .overlay import CONFIRMED, ERROR, PROCESSING, RECORDING, Overlay, level_from_rms
from .paths import DOCKER_UNAVAILABLE_MSG, is_frozen
from .pipeline import process_text
from .polish import polish, read_selection
from .polish_presets import resolve_instruction
from .remote_mic import RemoteAudioSource, RemoteMicServer
from .transcriber import create_transcriber
from .tray import SystemTray


def _play(beep: Callable[[], None]) -> None:
    """Play a feedback sound on its own thread, so neither the hook nor the loop waits for it."""
    threading.Thread(target=beep, daemon=True).start()


# A toggle take shorter than this is an accidental double press: dropped, with a neutral cue.
MIN_TOGGLE_TAKE_S = 0.3
# A polish chord held shorter than this is a tap (nothing was said); longer means an instruction that a
# missing server would lose, so the selection is left alone.
QUICK_TAP_S = 0.6


def _feedback_mode(config: object) -> str:
    """`sound`, `visual` or `both`: how dictation cues are shown; `sound` when the config says nothing."""
    mode = getattr(getattr(config, "feedback", None), "mode", "sound")
    return mode if mode in FEEDBACK_MODES else "sound"


@dataclass(frozen=True)
class _Recording:
    """One finished recording, as saved with whatever it turned into."""

    audio: bytearray
    duration_ms: int
    confidence: float | None
    transcribe_ms: int


# A key press older than this before the recording started is not the start of this take.
MAX_TAKE_LEAD_S = 10.0

logger = logging.getLogger(__name__)


def apply_startup_fallback(inference: InferenceConfig, device: str) -> None:
    """Swap a model that cannot start for Parakeet, for this session.

    The custom-server slot without a URL cannot build a client, so it falls back instead of crashing
    before the dashboard is up. The TOML is not rewritten here.
    """
    info = MODEL_REGISTRY.get(inference.model, {})
    if not (info.get("custom") and not inference.custom.url):
        return
    fallback = DEFAULT_MODEL
    logger.warning(
        "Configured model %s needs [inference.custom].url in config.toml. Falling back to %s for this session.",
        inference.model,
        fallback,
    )
    inference.model = fallback
    if model_needs_docker(fallback, device):
        inference.server_url = str(MODEL_REGISTRY[fallback]["default_url"])


class NexusVoxApp:
    """Orchestrates the push-to-talk transcription pipeline."""

    # When the hotkey last went down: a take starts here, not when the connection to the server is up.
    _press_time = 0.0
    _overlay: Overlay | None = None  # the visual feedback window; a test App built without __init__ has none

    def __init__(self, config: Config) -> None:
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

        # Resolve device once at startup. A configured model that cannot start (a custom server
        # without a URL) falls back to Parakeet without rewriting the user's TOML.
        self._device = resolve_device(config.inference.device)
        logger.info(
            "Resolved compute device: %s (requested: %s)",
            self._device,
            config.inference.device,
        )
        apply_startup_fallback(config.inference, self._device)

        # Optional extension packages (entry points); a broken one is logged and skipped. In the background,
        # so a heavy package never delays the hotkey; the dashboard waits for it when it needs the registry.
        threading.Thread(target=load_extensions, name="load-extensions", daemon=True).start()

        # Components
        self._audio = AudioCapture(config.audio)
        self._overlay = Overlay()
        if _feedback_mode(config) != "sound":
            self._overlay.warm_up()
        self._audio.on_rms = self._on_rms
        self._audio.rms_wanted = self._shows_overlay
        self._transcriber = create_transcriber(
            config.inference,
            device=self._device,
            auto_fallback=config.inference.device == "auto",
        )
        self._db = Database(config.database)
        # Statistics writes (dictionary hits, learn-from-edits outcomes) run here, one at a time and in order,
        # so a slow or locked database never delays the paste or the hotkey.
        self._stats_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stats")
        # Reads the field of the last paste and queues the user's corrections as dictionary
        # suggestions. `[learn_from_edits].enabled` is checked per paste, so the toggle is live.
        self._edit_watcher = EditWatcher(
            self._db.add_dictionary_suggestions,
            self._db.get_dictionary_entries,
            on_outcome=lambda outcome: self._stats_pool.submit(self._store_stat, self._db.record_learn_event, outcome),
        )

        self._hotkey = HotkeyListener(
            config=config.hotkey,
            on_activate=self._on_hotkey_activate,
            on_deactivate=self._on_hotkey_deactivate,
            command_config=config.os_control,
            on_command=self._on_hotkey_command,
            on_cancel=self._on_hotkey_cancel,
        )
        # The latest hotkey stop: (marker at the stop, cancelled by Escape, monotonic time of the stop gesture).
        # Captured in the stop callback, so a quick next take cannot reset what this one was marked as before
        # its cycle reads it. One slot, not a queue: start/stop events collapse when several presses arrive while
        # a cycle runs, and a queue would then go out of step with its cycles for good.
        self._stop_info: tuple[str | None, bool, float | None] | None = None

        # Meeting recording has its own toggle hotkey; dictation is untouched while a meeting runs.
        self._meeting_hotkey = MeetingHotkey(config.meeting, self._on_meeting_hotkey)
        self._meeting_toggle_lock = threading.Lock()

        # Mutes other apps' microphone sessions while the hotkey is held. Always built
        # (None only when pycaw is missing); `[mic_guard].enabled` is checked per hold so
        # the dashboard toggle is live. Release is unconditional and idempotent.
        self._mic_guard: MicGuard | None = create_mic_guard(
            Path(config.database.path).parent,
            watchdog_s=config.mic_guard.watchdog_s,
            still_held=self._hotkey.still_recording,
        )

        self._tray = SystemTray(
            on_quit=self._on_quit,
            on_toggle_translate=self._toggle_translate,
            get_translate=lambda: self._config.translator.enabled,
            on_open_dashboard=self._open_dashboard,
        )

        # Signals between hotkey thread and asyncio loop
        self._recording = False
        self._record_start_event: asyncio.Event | None = None
        self._record_stop_event: asyncio.Event | None = None

        # A phone recording in place of the microphone ([remote_mic]). The server is
        # built in `_run`, on the loop it serves from. `_remote_recording` is set from the
        # phone's start until the cycle is over; the hotkey is ignored meanwhile. The text
        # goes back to the phone (`_remote_text`) instead of being typed here.
        self._remote_audio = RemoteAudioSource()
        self._remote_mic: RemoteMicServer | None = None
        self._remote_recording = False
        self._remote_cancelled = False
        self._remote_text = ""
        self._cycle_running = False
        # What the last dictation pasted, for the paste key. In memory only: the saved
        # transcript is the spoken text, which differs once it was translated.
        self._last_typed = ""

        # Model switch state
        self._switch_status: str = "idle"
        self._switch_error: str | None = None

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
        """Called from the hotkey thread when Escape cancels the recording: nothing is pasted."""
        self._stop_info = (None, True, None)
        if self._mic_guard is not None:
            self._mic_guard.release_async()
        if self._loop is not None and self._record_stop_event is not None:
            self._loop.call_soon_threadsafe(self._record_stop_event.set)

    def _next_stop(self) -> tuple[str | None, bool, float | None]:
        """What the latest hotkey stop was: (marker, cancelled, time of the gesture). Read once per take."""
        info, self._stop_info = self._stop_info, None
        return info if info is not None else (None, False, None)

    async def _offline_stop(self) -> None:
        """The transcription server is unreachable: paste-last and a quick polish tap need none.

        The error cue goes out at once, so a Hold user is not left dictating into nothing. A recording that can
        still get a marker (Hold, or a marker already set) then waits for its end; a Toggle recording without a
        marker has no way to say what it wants and ends here. A polish held longer than a tap carried an
        instruction that is lost: the selection stays as it is.
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
        held_s = (at or time.monotonic()) - self._press_time
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

    @staticmethod
    def _store_stat(write: Callable[..., None], *args) -> None:
        try:
            write(*args)
        except Exception:
            logger.exception("Could not store a statistic")

    def get_learn_status(self) -> dict:
        """How the pastes since start ended for learning from edits, for the dashboard."""
        return self._edit_watcher.status()

    def get_switch_status(self) -> dict:
        """Return the current model-switch status for the dashboard.

        Once no switch is in flight (idle, or just ready), an in-process model that is still downloading
        or loading reports that (`downloading` / `loading`), and one that failed to load reports `error`
        with the reason, so the first start of CPU Parakeet neither looks done nor hung.
        """
        if self._switch_status in ("idle", "ready"):
            load = self._transcriber.load_status()
            if load and load["state"] in ("downloading", "loading"):
                return {"status": load["state"], "error": None}
            if load and load["state"] == "error":
                return {"status": "error", "error": load["error"]}
        return {"status": self._switch_status, "error": self._switch_error}

    async def switch_model(self, model_id: str) -> None:
        """Switch to a different transcription model.

        In-process (Parakeet or Whisper on CPU) and custom-server switches skip Docker entirely;
        GPU-backed switches stop the old container, start the new one, and wait for health.
        """
        if model_id not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model: {model_id}")
        if self._recording:
            raise RuntimeError("Cannot switch model while recording")
        if self._switch_status not in ("idle", "ready", "error"):
            raise RuntimeError("Model switch already in progress")

        old_model = self._config.inference.model
        if old_model == model_id:
            return
        old_url = self._config.inference.server_url

        new_info = MODEL_REGISTRY[model_id]
        old_info = MODEL_REGISTRY[old_model]
        loop = asyncio.get_event_loop()

        new_needs_docker = model_needs_docker(model_id, self._device)
        old_was_docker = self._transcriber.needs_docker
        if new_needs_docker and is_frozen():
            raise RuntimeError(DOCKER_UNAVAILABLE_MSG)

        try:
            self._switch_status = "stopping"
            await self._transcriber.disconnect()

            if old_was_docker:
                await loop.run_in_executor(None, docker_ctl.stop_profile, old_info["docker_profile"])

            if new_needs_docker:
                self._switch_status = "starting"
                await loop.run_in_executor(None, docker_ctl.start_profile, new_info["docker_profile"])
                self._switch_status = "waiting"
                healthy = await loop.run_in_executor(None, docker_ctl.wait_for_healthy, new_info["health_url"])
                if not healthy:
                    raise RuntimeError(f"Container for {model_id} did not become healthy")

            self._config.inference.model = model_id
            if new_needs_docker:
                self._config.inference.server_url = str(new_info["default_url"])
            self._transcriber = create_transcriber(
                self._config.inference,
                device=self._device,
                auto_fallback=self._config.inference.device == "auto",
            )
            save_config(self._config)

            self._switch_status = "ready"
            logger.info("Switched to model: %s", model_id)
            if self._warms_up():
                asyncio.create_task(self._preload_transcriber())

            async def _reset_status():
                await asyncio.sleep(5)
                self._switch_status = "idle"
                self._switch_error = None

            asyncio.ensure_future(_reset_status())

        except Exception as exc:
            self._switch_status = "error"
            self._switch_error = str(exc)
            logger.exception("Model switch failed")
            # Keep the config on the model that is actually serving.
            self._config.inference.model = old_model
            self._config.inference.server_url = old_url
            # Fallback: restart the old container if it was Docker-backed.
            try:
                if old_was_docker:
                    await loop.run_in_executor(None, docker_ctl.start_profile, old_info["docker_profile"])
                    await loop.run_in_executor(None, docker_ctl.wait_for_healthy, old_info["health_url"])
                self._transcriber = create_transcriber(
                    self._config.inference,
                    device=self._device,
                    auto_fallback=self._config.inference.device == "auto",
                )
            except Exception:
                logger.exception("Fallback restart also failed")

    def _normalize_pcm(self, pcm_bytes: bytes | bytearray) -> bytes:
        """Peak-normalize a PCM16 buffer to 90% of int16 max for louder playback."""
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        peak = np.max(np.abs(samples))
        if peak > 0:
            samples = np.clip(samples * (29491.0 / peak), -32768, 32767)
        return samples.astype(np.int16).tobytes()

    def _store_audio(self, pcm_buffer: bytearray, record_id: int) -> None:
        """Keep the recording's WAV next to the database, unless `[database].store_audio` is off.

        The flag is read per recording, so the dashboard toggle is live. The transcript
        row is saved either way; only the playback file is skipped.
        """
        if not self._config.database.store_audio:
            return
        self._db.update_audio_path(record_id, self._save_audio_wav(pcm_buffer, record_id))

    def _save_audio_wav(self, pcm_buffer: bytearray, record_id: int) -> str:
        """Write a PCM16 buffer to a WAV file and return its relative path."""
        db_dir = Path(self._config.database.path).parent
        audio_dir = db_dir / self._config.database.audio_dir
        os.makedirs(audio_dir, exist_ok=True)
        filename = f"{record_id}_{int(time.time())}.wav"
        filepath = audio_dir / filename
        with wave.open(str(filepath), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._config.audio.sample_rate)
            wf.writeframes(self._normalize_pcm(pcm_buffer))
        return f"{self._config.database.audio_dir}/{filename}"

    @staticmethod
    def _begin_take(start: float) -> int | None:
        """Tell a running meeting that a dictation take began; the microphone records it too, and what happens to
        it is decided when the meeting is transcribed. Blocking (locks, file write): run it in an executor."""
        api = running_dashboard_api()
        return api.begin_dictation(start) if api is not None else None

    @staticmethod
    def _end_take(token: int | None, start: float, end: float) -> None:
        """Tell a running meeting that the take ended. Blocking, like `_begin_take`."""
        api = running_dashboard_api()
        if api is not None:
            api.end_dictation(token, start, end)

    async def _transcription_cycle(self, remote: bool = False) -> None:
        """Run one full transcription cycle: connect, record, transcribe, inject.

        `remote` records from the phone ([remote_mic]) instead of the microphone; it is
        never a command recording and leaves the hotkey state alone.
        """
        recording_stopped = False
        loop = asyncio.get_running_loop()
        take: tuple[int | None, float] | None = None  # (meeting token, start) while a take is open
        audio: AudioCapture | RemoteAudioSource | None = None  # set once recording started
        stream_task: asyncio.Task | None = None

        async def end_take(end: float) -> None:
            nonlocal take
            if take is not None:
                (token, start), take = take, None
                await loop.run_in_executor(None, self._end_take, token, start, end)

        try:
            # Connect to inference server BEFORE signalling readiness so the
            # WebSocket handshake doesn't race against hotkey release.
            try:
                await self._transcriber.connect()
            except Exception:
                if remote:
                    raise
                logger.exception("Could not reach the transcription server")
                with contextlib.suppress(Exception):
                    await self._transcriber.disconnect()
                await self._offline_stop()
                return

            # Clear any stop event that arrived while we were connecting.
            # If the user already released the keys, skip this cycle. A phone recording
            # is not skipped: its audio was buffered while we connected, and the stop
            # event stays set so the wait below returns at once.
            if not remote and self._record_stop_event.is_set():
                self._record_stop_event.clear()
                skip_mark, skip_cancelled, _ = self._next_stop()
                logger.info("Hotkey released before recording could start, skipping")
                await self._transcriber.disconnect()
                await self._hide_os_control()
                if skip_cancelled:
                    return
                if skip_mark is None and self._hotkey.mode == "toggle":
                    self._cue(beep_stop, PROCESSING)  # an accidental double press is not silent
                if skip_mark == PASTE_LAST:
                    # A quick tap of the paste chord (or one queued behind a running
                    # cycle) needs no recording: paste the last text all the same.
                    await self._paste_last_text()
                elif skip_mark == POLISH:
                    # A quick tap of the polish chord: nothing was said, so the default
                    # instruction; there is no recording, so its row has no audio.
                    await self._polish_selection("", None)
                return

            # Now we are ready — start recording
            self._tray.set_active(True)
            if remote:  # the phone shows its own state
                _play(beep_start)
            else:
                self._cue(beep_start, RECORDING)
            logger.info("Recording started")
            start_time = time.monotonic()

            # Start audio capture
            audio = self._remote_audio if remote else self._audio
            audio.start()
            if not remote:
                # From the key press if the connection took a while, so the first words are inside the range.
                pressed = (
                    self._press_time if start_time - MAX_TAKE_LEAD_S <= self._press_time <= start_time else start_time
                )
                take = (await loop.run_in_executor(None, self._begin_take, pressed), pressed)

            # Stream audio chunks until recording stops
            stream_task = asyncio.create_task(self._stream_audio(audio))

            # Wait for hotkey release
            # The phone ends its own recording; a cap here would send `done` while it still streams.
            cap_s = None if remote else self._config.hotkey.max_recording_s or None
            try:
                await asyncio.wait_for(self._record_stop_event.wait(), cap_s)
                self._record_stop_event.clear()
                mark, cancelled, stop_at = (None, False, None) if remote else self._next_stop()
            except TimeoutError:
                # Nothing ends a forgotten recording: stop it as if the user had, and say so.
                logger.warning("Recording stopped after the %s s maximum, processing what was said", cap_s)
                mark, cancelled, stop_at = (None, False, None) if remote else (self._hotkey.mark, False, None)
                if not remote:
                    self._hotkey.cancel()
                    if self._mic_guard is not None:
                        self._mic_guard.release_async()
            recording_stopped = True

            # Stop audio (sends None sentinel, ending the stream task)
            audio.stop()
            audio_buffer = await stream_task

            if cancelled:
                logger.info("Recording cancelled with Escape, nothing to transcribe")
                self._tray.set_active(False)
                await self._transcriber.disconnect()
                await self._hide_os_control()
                return

            stop_time = time.monotonic()
            duration_ms = int((stop_time - start_time) * 1000)
            await end_take(stop_time)
            logger.info("Recording stopped (duration=%dms)", duration_ms)

            if remote and self._remote_cancelled:
                logger.info("Remote recording cancelled, nothing to transcribe")
                self._tray.set_active(False)
                await self._transcriber.disconnect()
                return

            self._tray.set_active(False)
            if remote:
                _play(beep_stop)
            else:
                self._cue(beep_stop, PROCESSING)

            # Measured to the stop gesture, not to the end of the grace window that follows a modifier-only chord.
            take_s = (stop_at - start_time) if stop_at else duration_ms / 1000
            if not remote and mark is None and self._hotkey.mode == "toggle" and take_s < MIN_TOGGLE_TAKE_S:
                logger.info("Toggle take under %d ms, dropped", MIN_TOGGLE_TAKE_S * 1000)
                await self._transcriber.disconnect()
                return

            if mark == PASTE_LAST:
                # The paste key was tapped during the hold: drop this recording and paste
                # the last text again instead of transcribing.
                await self._transcriber.disconnect()
                await self._paste_last_text()
                return

            # Finalize transcription
            try:
                result = await self._transcriber.finish()
            except Exception:
                if remote or mark != POLISH or stop_at is None or stop_at - self._press_time >= QUICK_TAP_S:
                    raise
                # A polish tap needs no transcript (HTTP backends only fail here, not in connect()).
                logger.exception("Transcription failed; polishing with the default instruction")
                await self._transcriber.disconnect()
                await self._polish_selection("", None)
                return
            transcribe_ms = int((time.monotonic() - stop_time) * 1000)
            logger.info("Transcript received after %dms", transcribe_ms)

            recording = _Recording(audio_buffer, duration_ms, result.confidence, transcribe_ms)

            if mark == COMMAND:
                # A command recording (command key pressed during the hold) is never
                # typed: the transcript goes to os-control, which also hides
                # its widget again when there is nothing to act on.
                command_text = result.text.strip()
                reply = await asyncio.get_running_loop().run_in_executor(
                    None, self._send_os_command, command_text or "::hide"
                )
                if reply is None or reply.startswith("error:"):
                    self._cue(beep_error, ERROR)
                if command_text:
                    self._save_command(recording, f"[os-control] {command_text}", command_text)
                await self._transcriber.disconnect()
                return

            if mark == POLISH:
                # The polish key was tapped during the hold: what was said (maybe nothing)
                # is an instruction for the selected text, not text to type.
                await self._transcriber.disconnect()
                await self._polish_selection(result.text.strip(), recording)
                return

            raw_text = result.text.strip()
            if not raw_text:
                logger.info("Empty transcription, nothing to inject")
                return
            loop = asyncio.get_running_loop()

            self._hook_budget_s = HOOK_TIMEOUT_S
            # A phone recording only wants its text back, never a PC action.
            if not remote:
                nexus_cmd = parse_nexus_command(raw_text)
                if nexus_cmd is not None and nexus_cmd.action == "flag" and self._config.experimental.flag_command:
                    self._flag_last_transcription()
                    await self._transcriber.disconnect()
                    return
                label = await self._run_command(raw_text, nexus_cmd)
                if label is not None:
                    self._save_command(recording, label, raw_text)
                    await self._transcriber.disconnect()
                    return

            processed = await loop.run_in_executor(
                None,
                functools.partial(
                    process_text,
                    raw_text,
                    self._config,
                    self._db.get_dictionary_entries(),
                    remote=remote,
                    extend_fn=None if remote else self._extend_dictation,
                    protect_fn=functools.partial(self._protect_dictation, remote=remote),
                ),
            )
            done_at = None  # when the text was in place; the clipboard restore after it is not latency
            target_app = None  # process name of the app the text lands in; never the window title
            if remote:
                # The phone inserts it into its own focused field.
                self._remote_text = processed.typed
            else:
                try:
                    done_at, target_app = await loop.run_in_executor(
                        None, self._paste, processed.typed, self._config.injection_delay_ms, processed.press_enter
                    )
                    pasted = True
                except ClipboardError as exc:
                    # The transcript is still saved below, and the paste key pastes the text.
                    logger.warning("Could not paste the dictation (%s); the paste key pastes it", exc)
                    self._cue(beep_error, ERROR)
                    pasted = False
                if processed.typed:  # an Enter-only dictation keeps the last text
                    self._last_typed = processed.typed
                if self._config.learn_from_edits.enabled and processed.typed:
                    if not pasted:
                        self._edit_watcher.skip("clipboard")
                    elif not processed.learnable:
                        self._edit_watcher.skip("processed")
                    else:
                        self._edit_watcher.watch(
                            processed.typed, self._config.learn_from_edits.window_s, processed.spoken
                        )
            latency_ms = int(((done_at or time.monotonic()) - stop_time) * 1000)
            # lingua loads its models on first use: keep that off the event loop.
            language = await loop.run_in_executor(None, stored_language, raw_text)
            record = self._db.save_transcription(
                text=processed.saved,
                language=language,
                duration_ms=duration_ms,
                confidence=result.confidence,
                model=self._transcriber.model,
                translated=processed.translation.translated,
                translate_source=processed.translation.source,
                translate_ms=processed.translation.ms,
                translate_error=processed.translation.error,
                transcribe_ms=transcribe_ms,
                latency_ms=latency_ms,
                cleanup_status=processed.cleanup.status,
                cleanup_ms=processed.cleanup.ms,
                app=target_app,
            )
            logger.info(
                "%s after %dms: %s", "Sent to phone" if remote else "Injected", latency_ms, Text(processed.typed)
            )
            self._stats_pool.submit(self._store_stat, self._db.record_dictionary_hits, processed.dictionary_hits)
            self._store_audio(audio_buffer, record.id)

        except Exception:
            logger.exception("Transcription cycle failed")
            await self._abort_recording(audio, stream_task, recording_stopped)
            self._tray.set_active(False)
            if not remote:
                self._cue(None, ERROR)
            if not recording_stopped and not remote:
                # The recording this intent belonged to is gone. Without this, toggle
                # mode would treat the next press as "stop" (and keep the mic guard
                # muted); hold mode would queue a stale stop that skips the next cycle.
                self._hotkey.cancel()
            if not remote:
                # `_hotkey.command` may still describe the last hotkey recording.
                await self._hide_os_control()
            await self._transcriber.disconnect()
        finally:
            await end_take(time.monotonic())  # a take that died before its stop is stored too
            # Key-up already queued a release; this covers a cycle that died mid-hold.
            # Skip it while the hotkey is held again: the user may already be in the
            # next dictation (pressed during transcription), and its hold must stand.
            if self._mic_guard is not None and not self._hotkey.active:
                self._mic_guard.release_async()
            if self._overlay is not None and not remote:
                if not self._hotkey.active:
                    self._overlay.show("done")  # the text is typed (or nothing was); an error stays its moment
                elif self._shows_overlay():
                    # Held again: the next take starts when its connection is up, but the user is speaking now.
                    self._overlay.show(RECORDING)

    def _shows_overlay(self) -> bool:
        """Whether the waveform is wanted and its window is alive."""
        overlay = self._overlay
        return overlay is not None and not overlay.failed and _feedback_mode(getattr(self, "_config", None)) != "sound"

    def on_feedback_mode(self, mode: str) -> None:
        """The dashboard changed `[feedback].mode`: build the overlay window now, not mid-dictation."""
        if mode != "sound" and self._overlay is not None:
            self._overlay.warm_up()

    def _on_rms(self, rms: float) -> None:
        """Microphone level from the audio thread, for the waveform; only a recording shows it."""
        if self._overlay is not None:
            self._overlay.level(level_from_rms(rms))

    def _cue(self, beep: Callable[[], None] | None, state: str) -> None:
        """Signal a state the way `[feedback].mode` says: the chime, the overlay, or both.

        `state` is `RECORDING`, `PROCESSING`, `ERROR` or `CONFIRMED`. Meeting recordings and the phone
        never come through here for the overlay. A window that could not start or died leaves the sounds
        on, whatever the mode."""
        mode = _feedback_mode(getattr(self, "_config", None))
        overlay = self._overlay
        visual = overlay is not None and not overlay.failed and mode != "sound"
        if beep is not None and (mode != "visual" or not visual):
            _play(beep)
        if visual:
            overlay.show(state)

    _active_app_failed = False

    @classmethod
    def _active_app(cls) -> ActiveApp:
        try:
            from .window_manager import foreground_app  # lazy, like the other window_manager uses here

            process, title = foreground_app()
        except Exception:
            # Every dictation asks; one traceback is enough when it keeps failing.
            level = logging.DEBUG if cls._active_app_failed else logging.ERROR
            logger.log(level, "Could not read the active app", exc_info=True)
            cls._active_app_failed = True
            return ActiveApp()
        return ActiveApp(process, title)

    def _paste(self, text: str, delay_ms: int, press_enter: bool) -> tuple[float, str | None]:
        """Type the text at the cursor (runs in the executor); also the process name of the app it lands in.

        The app is read right before the paste, and only a paste that went through returns it, so a dictation
        the clipboard refused is never credited to an app. Only the process name is kept, never the title.
        """
        process = self._active_app().process.lower() if text else ""
        return inject_text(text, delay_ms, press_enter), process or None

    # What is left of this dictation's `HOOK_TIMEOUT_S` for the extensions' hooks: `on_command` uses some of it
    # before the pipeline runs, `on_dictation` gets the rest. Reset for every dictation (one cycle at a time).
    _hook_budget_s: float = HOOK_TIMEOUT_S

    @contextlib.contextmanager
    def _charged(self) -> Iterator[None]:
        """Charge the time of the block to this dictation's hook budget."""
        started = time.monotonic()
        try:
            yield
        finally:
            self._hook_budget_s = max(0.0, self._hook_budget_s - (time.monotonic() - started))

    def _protect_dictation(self, text: str, *, remote: bool = False) -> tuple[str, Callable[[str], str | None]] | None:
        """The extensions' `protect` on a dictation, phone ones included (runs in the executor with the pipeline).

        Returns `(protected text, restore)` or None. `restore` draws on what is left of the hook budget when the
        pipeline calls it, not on the time cleanup and translation took in between.
        """
        registry = loaded_extensions()  # None while the packages still load: no protection, never wait
        if registry is None or not registry.has_protect_hook():
            return None  # no thread, no window lookup: an extension without the hook costs a dictation nothing
        with self._charged():
            answer = registry.protect(text, ActiveApp() if remote else self._active_app(), budget_s=self._hook_budget_s)
        if answer is None:
            return None
        protected, restore = answer

        def charged_restore(restoring: str) -> str | None:
            with self._charged():
                return restore(restoring, budget_s=self._hook_budget_s)

        return protected, charged_restore

    def _extend_dictation(self, text: str) -> str:
        """The extensions' say on a dictation about to be typed (runs in the executor with the pipeline)."""
        registry = loaded_extensions()  # None while the packages still load: type without them, never wait
        if registry is None or not registry.extensions:
            return text
        return registry.dictation(text, self._active_app(), budget_s=self._hook_budget_s)

    def _save_command(
        self,
        recording: _Recording | None,
        label: str,
        spoken: str,
        *,
        polish_status: str | None = None,
        polish_ms: int | None = None,
    ) -> None:
        """Save a dictation that ran a command instead of being typed, as ``label``.

        A quick tap of the polish chord has no *recording*: its row has no audio, duration or model.
        """
        record = self._db.save_transcription(
            text=label,
            language=stored_language(spoken),
            duration_ms=recording.duration_ms if recording else 0,
            confidence=recording.confidence if recording else None,
            model=self._transcriber.model if recording else None,
            transcribe_ms=recording.transcribe_ms if recording else None,
            app=COMMAND_APP,
            polish_status=polish_status,
            polish_ms=polish_ms,
        )
        if recording:
            self._store_audio(recording.audio, record.id)

    async def _paste_last_text(self) -> None:
        """Paste what the last dictation pasted, once more; the error beep when there is none yet."""
        if not self._last_typed:
            logger.info("Paste last: nothing pasted yet in this session")
            self._cue(beep_error, ERROR)
            return
        logger.info("Paste last: %s", Text(self._last_typed))
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, inject_text, self._last_typed, self._config.injection_delay_ms
            )
        except ClipboardError as exc:
            logger.warning("Paste last: could not paste (%s)", exc)
            self._cue(beep_error, ERROR)

    async def _polish_selection(self, instruction: str, recording: _Recording | None) -> None:
        """Rewrite the selected text by *instruction* through the [llm] model and paste it over the selection.

        Nothing selected, or the model unreachable or failing: the error beep, and the
        selection stays as it was. When the focus moved to another window while the model
        worked, nothing is pasted (error beep); the paste key still pastes the reply.
        """
        loop = asyncio.get_running_loop()
        try:
            selection = await loop.run_in_executor(
                None, read_selection, self._config.injection_delay_ms, self._config.polish.in_terminals
            )
        except ClipboardError as exc:
            logger.warning("Polish: could not copy the selection (%s)", exc)
            self._cue(beep_error, ERROR)
            return
        if not selection.strip():
            logger.info("Polish: nothing selected")
            self._cue(beep_error, ERROR)
            return
        spoken = instruction
        window = foreground_window()  # before any hook runs: the paste checks the focus did not move meanwhile
        registry = loaded_extensions()
        if registry is not None and registry.extensions:
            handled = await loop.run_in_executor(
                None, registry.selection_command, selection, spoken, self._active_app()
            )
            if handled is not None:
                await self._paste_over_selection(
                    handled, window, "extension", spoken, spoken, recording, status="extension", model_ms=None
                )
                return
        instruction, preset = resolve_instruction(spoken, self._db.get_polish_presets())
        if preset:
            logger.info("Polish: preset %r", preset)
        detail = preset or spoken or "default"
        started = time.perf_counter()  # not monotonic(): its tick is ~15 ms on Windows
        try:
            rewritten = await loop.run_in_executor(None, polish, selection, instruction, self._config.llm)
        except LlmError as exc:
            logger.warning("Polish: model at %s failed (%s); the selection stays as it was", self._config.llm.url, exc)
            self._cue(beep_error, ERROR)
            await self._save_polish(
                recording,
                "polish",
                detail,
                spoken,
                ok=False,
                status="timeout" if exc.timed_out else "failed",
                model_ms=round((time.perf_counter() - started) * 1000),
            )
            return
        model_ms = round((time.perf_counter() - started) * 1000)
        await self._paste_over_selection(
            rewritten, window, "polish", detail, spoken, recording, status="applied", model_ms=model_ms
        )

    async def _paste_over_selection(
        self,
        rewritten: str,
        window: int,
        kind: str,
        detail: str,
        spoken: str,
        recording: _Recording | None,
        *,
        status: str,
        model_ms: int | None,
    ) -> None:
        """Paste *rewritten* over the still-selected text, unless the focus left *window*; save the outcome.

        Nothing pasted (the focus moved, the clipboard refused) is saved as ``failed``, however the reply came.
        """
        self._last_typed = rewritten
        pasted = False
        if foreground_window() != window:
            logger.info("Polish: the focus moved to another window, not pasting (the paste key pastes it)")
            self._cue(beep_error, ERROR)
        else:
            # The selection is still selected, so pasting replaces it; Ctrl+Z in the app undoes it.
            try:
                await asyncio.get_running_loop().run_in_executor(None, inject_text, rewritten, 0)
                pasted = True
            except ClipboardError as exc:
                logger.warning("Polish: could not paste the reply (%s); the paste key pastes it", exc)
                self._cue(beep_error, ERROR)
        await self._save_polish(
            recording, kind, detail, spoken, ok=pasted, status=status if pasted else "failed", model_ms=model_ms
        )

    async def _save_polish(
        self,
        recording: _Recording | None,
        kind: str,
        detail: str,
        spoken: str,
        *,
        ok: bool,
        status: str,
        model_ms: int | None,
    ) -> None:
        """Save a polish (or extension) attempt, off the event loop, as ``[kind] detail`` or ``[kind failed] detail``.

        A failed attempt gets its own label so the history does not show it as a polish that happened.
        """
        label = f"[{kind}{'' if ok else ' failed'}] {detail}"
        save = functools.partial(self._save_command, recording, label, spoken, polish_status=status, polish_ms=model_ms)
        await asyncio.get_running_loop().run_in_executor(None, save)

    def _flag_last_transcription(self) -> None:
        """ "nexus flag" (``[experimental].flag_command``): flags the most recent transcription for review."""
        last_tid = self._db.get_last_transcription_id()
        if last_tid is None:
            logger.info("Nexus flag: no transcription to flag")
            return
        self._db.flag_transcription(last_tid)
        logger.info("Nexus flag: flagged transcription id=%d", last_tid)
        self._cue(beep_flag, CONFIRMED)

    def _extension_command(self, raw_text: str) -> tuple[str, bool] | None:
        """An extension's `on_command` answer for *raw_text* (runs in the executor), or None: nobody took it."""
        registry = loaded_extensions()  # None while the packages still load: no extension command, never wait
        if registry is None or not registry.has_command_hook():
            return None  # no thread, no window lookup: an extension without the hook costs a dictation nothing
        try:
            with self._charged():
                return registry.command(raw_text, self._active_app(), budget_s=self._hook_budget_s)
        except Exception:
            # A broken extension step must never lose the dictation (process_text guards on_dictation the same way).
            logger.exception("The extension command step failed; handling the dictation without it")
            return None

    async def _run_command(self, raw_text: str, nexus_cmd: NexusCommand | None) -> str | None:
        """Run the command *raw_text* is, if any. Returns the label to save it under, or None to type it.

        Checked in order: an extension's `on_command` (it consumes the dictation), "nexus assistant <text>"
        (when enabled; falls through to typing if nothing answers), nexus window commands (when enabled).
        """
        loop = asyncio.get_running_loop()
        handled = await loop.run_in_executor(None, self._extension_command, raw_text)
        if handled is not None:
            label, opened = handled
            if not opened:
                self._cue(beep_error, ERROR)
            return label

        assistant_text = parse_assistant_command(raw_text) if self._config.assistant.enabled else None
        if assistant_text is not None:
            try:
                reply = await loop.run_in_executor(None, send_to_assistant, assistant_text, self._config.assistant)
            except OSError as exc:
                logger.warning("Assistant not reachable (%s); typing the text instead", exc)
                self._cue(beep_error, ERROR)
            else:
                if reply.startswith("error:"):
                    logger.warning("Assistant refused the command: %s", Text(reply))
                    self._cue(beep_error, ERROR)
                return f"[assistant] {assistant_text}"

        if (
            nexus_cmd is not None
            and nexus_cmd.action in WINDOW_ACTIONS
            and self._config.experimental.os_commands
            and self._config.os_commands.enabled
        ):
            await loop.run_in_executor(None, execute_nexus_command, nexus_cmd, self._config.os_commands)
            logger.info("Nexus command: %s %s", nexus_cmd.action, nexus_cmd.app_name)
            return f"[nexus] {nexus_cmd.action} {nexus_cmd.app_name}"
        return None

    async def _stream_audio(self, audio: AudioCapture | RemoteAudioSource) -> bytearray:
        """Stream audio chunks from the mic or the phone to the transcription server, returning the full buffer.

        When sending fails, the rest of the recording is still read up to its end marker (and kept), then the
        error is raised. Stopping at the failure would leave the unread chunks and the marker in the audio queue,
        and the next recording would start by reading them: every dictation after it would be the previous one.
        """
        buffer = bytearray()
        count = 0
        failure: Exception | None = None
        async for chunk in audio.chunks():
            buffer.extend(chunk)
            if failure is not None:
                continue
            try:
                await self._transcriber.send_audio_chunk(chunk)
                count += 1
            except Exception as exc:
                logger.exception("Error streaming audio")
                failure = exc
        logger.info("Streamed %d audio chunks to server", count)
        if failure is not None:
            raise failure
        return buffer

    async def _abort_recording(
        self,
        audio: AudioCapture | RemoteAudioSource | None,
        stream_task: asyncio.Task | None,
        recording_stopped: bool,
    ) -> None:
        """After a failed cycle: end a stream that still runs and close the microphone it was reading.

        A cycle that failed before the stop must not leave its stream task reading the audio queue (it would
        take the next recording's chunks), nor the microphone open.
        """
        if audio is None:
            return
        try:
            if not recording_stopped:
                audio.stop()
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
            if stream_task is not None:
                await asyncio.gather(stream_task, return_exceptions=True)
        except Exception:
            logger.exception("Could not end the failed recording cleanly")

    def _open_dashboard(self) -> None:
        """Open the dashboard window."""
        open_dashboard(self._db, self._config, app=self)

    def open_dashboard_from_second_start(self) -> None:
        """A second start of NexusVox rang the doorbell: show the dashboard of this instance."""
        logger.info("A second NexusVox start was detected; opening the dashboard of this instance")
        self._open_dashboard()

    def _toggle_translate(self) -> None:
        """Switch translate-before-inject on or off, and keep it across restarts."""
        self._config.translator.enabled = not self._config.translator.enabled
        save_config(self._config)
        logger.info("Translate before inject: %s", "on" if self._config.translator.enabled else "off")

    def _on_quit(self) -> None:
        """Handle quit from system tray."""
        self._running = False
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _run(self) -> None:
        """Main async run loop."""
        self._loop = asyncio.get_event_loop()
        self._running = True
        self._record_start_event = asyncio.Event()
        self._record_stop_event = asyncio.Event()

        # Start components
        self._hotkey.start()
        self._meeting_hotkey.start()
        self._tray.start()

        logger.info(
            "NexusVox started. %s %s to talk.",
            "Press" if self._config.hotkey.mode == "toggle" else "Hold",
            describe_hotkey(self._config.hotkey),
        )

        if self._mic_guard is not None:
            # Repair what a killed previous run left muted, even if the guard has been
            # switched off since. The warm-up (one-off COM wrapper cost, otherwise paid
            # on the first key-down) is only worth it while the guard is enabled.
            await self._loop.run_in_executor(None, self._mic_guard.restore_leftovers)
            if self._config.mic_guard.enabled:
                await self._loop.run_in_executor(None, self._mic_guard.warm_up)

        if self._config.cleanup.level != "off":
            # Auto cleanup gives up after [cleanup].timeout_s; a cold model would cost the first dictations.
            llm_warm_up(self._config.llm)

        # For in-process transcribers (Whisper on CPU), eagerly load the model
        # so the first hotkey press doesn't pay the full load + download latency.
        # A custom server is remote and has nothing to preload.
        # Engine "auto" on a GPU machine warms up the Docker server (or the CPU model while it is down).
        if self._warms_up():
            asyncio.create_task(self._preload_transcriber())

        if self._config.remote_mic.enabled:
            self._remote_mic = RemoteMicServer(
                self._config.remote_mic,
                self._remote_audio,
                self._on_remote_start,
                self._on_remote_stop,
                self._on_remote_cancel,
            )
            asyncio.create_task(self._remote_mic.run())

        # Main loop: wait for hotkey press, run transcription cycle
        while self._running:
            await self._record_start_event.wait()
            self._record_start_event.clear()

            if not self._running:
                break

            self._cycle_running = True
            try:
                await self._transcription_cycle(remote=self._remote_recording)
            finally:
                self._cycle_running = False
                if self._remote_recording:
                    self._remote_recording = False
                    # A cycle that failed after the phone's stop leaves it set; the next
                    # hotkey cycle would take it as a release and skip itself.
                    self._record_stop_event.clear()
                    if self._remote_mic is not None:
                        self._remote_mic.finished(self._remote_text)

        # Cleanup
        await self._shutdown()

    def _is_inprocess(self) -> bool:
        """Whether the active model runs in this process; a custom server is remote and has nothing to load."""
        return not self._transcriber.needs_docker and not MODEL_REGISTRY[self._config.inference.model].get("custom")

    def _warms_up(self) -> bool:
        """Whether the active engine is warmed up in the background: in-process models load, "auto" warms the server."""
        return self._is_inprocess() or self._transcriber.warms_up_at_start

    async def _preload_transcriber(self) -> None:
        """Warm up an in-process transcriber so the first hotkey press is snappy."""
        try:
            logger.info("Preloading in-process transcriber model (first run may download)")
            await self._transcriber.warm_up()
            logger.info("Preload complete")
        except Exception:
            logger.exception("Preloading transcriber failed")

    async def _shutdown(self) -> None:
        """Clean up all components."""
        logger.info("Shutting down...")
        self._hotkey.stop()
        self._meeting_hotkey.stop()
        api = running_dashboard_api()
        if api is not None:
            api.shutdown_meeting()
        self._tray.stop()
        self._audio.stop()
        self._edit_watcher.stop()
        self._stats_pool.shutdown(wait=True)
        if self._remote_mic is not None:
            await self._remote_mic.close()
        if self._mic_guard is not None:
            self._mic_guard.release()
        await self._transcriber.disconnect()

    def run(self) -> None:
        """Start the application."""
        asyncio.run(self._run())
