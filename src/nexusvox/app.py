"""Main application orchestrator — wires hotkey, audio, transcriber, and injector."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import docker_ctl
from .assistant import parse_assistant_command, send_to_assistant, send_to_service
from .audio import AudioCapture
from .clipboard import ClipboardError
from .config import (
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
from .extensions import ActiveApp, load_extensions, loaded_extensions
from .feedback import beep_error, beep_flag, beep_start, beep_stop
from .hotkey import COMMAND, PASTE_LAST, POLISH, HotkeyListener, MeetingHotkey, describe_hotkey
from .injector import foreground_window, inject_text
from .lang_detect import detect_language
from .llm import LlmError
from .llm import warm_up as llm_warm_up
from .logsetup import Text
from .mic_guard import MicGuard, create_mic_guard
from .models import COMMAND_APP
from .os_commands import NexusCommand, execute_nexus_command, parse_nexus_command, parse_nexus_open
from .overlay import CONFIRMED, ERROR, PROCESSING, RECORDING, Overlay, level_from_rms
from .pipeline import process_text
from .polish import polish, read_selection
from .polish_presets import resolve_instruction
from .remote_mic import RemoteAudioSource, RemoteMicServer
from .snippets import Snippet, resolve_open_target
from .transcriber import create_transcriber
from .tray import SystemTray


def _play(beep: Callable[[], None]) -> None:
    """Play a feedback sound on its own thread, so neither the hook nor the loop waits for it."""
    threading.Thread(target=beep, daemon=True).start()


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


_CPU_FALLBACK_MODEL = "whisper-large-v3-turbo"
# A key press older than this before the recording started is not the start of this take.
MAX_TAKE_LEAD_S = 10.0

logger = logging.getLogger(__name__)


def apply_startup_fallback(inference: InferenceConfig, device: str) -> None:
    """Swap a model that cannot start on this machine for a bundled one, for this session.

    A GPU model on CPU falls back to Whisper in-process. The custom-server slot without
    a URL cannot build a client, so it falls back too instead of crashing before the
    dashboard is up. The TOML is not rewritten here.
    """
    info = MODEL_REGISTRY.get(inference.model, {})
    if device == "cpu" and info.get("requires_gpu"):
        reason = "requires a GPU but device is CPU"
    elif info.get("custom") and not inference.custom.url:
        reason = "needs [inference.custom].url in config.toml"
    else:
        return
    fallback = _CPU_FALLBACK_MODEL if device == "cpu" else InferenceConfig().model
    logger.warning("Configured model %s %s. Falling back to %s for this session.", inference.model, reason, fallback)
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

        # Resolve device once at startup. If the configured model requires a
        # GPU but we're on CPU, fall back to Whisper (without rewriting the
        # user's TOML — they can adjust in the dashboard later).
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
            language=config.language,
            auto_detect_language=config.auto_language_detection,
        )
        self._db = Database(config.database)
        # Reads the field of the last paste and queues the user's corrections as dictionary
        # suggestions. `[learn_from_edits].enabled` is checked per paste, so the toggle is live.
        self._edit_watcher = EditWatcher(self._db.add_dictionary_suggestions, self._db.get_dictionary_entries)

        self._hotkey = HotkeyListener(
            config=config.hotkey,
            on_activate=self._on_hotkey_activate,
            on_deactivate=self._on_hotkey_deactivate,
            command_config=config.os_control,
            on_command=self._on_hotkey_command,
        )

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
            on_toggle_language=self._toggle_language,
            get_language=lambda: self._config.language,
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
        if self._mic_guard is not None:
            self._mic_guard.release_async()
        if self._loop is not None and self._record_stop_event is not None:
            self._loop.call_soon_threadsafe(self._record_stop_event.set)

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

        In-process (Whisper-on-CPU) and custom-server switches skip Docker entirely;
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
                language=self._config.language,
                auto_detect_language=self._config.auto_language_detection,
            )
            save_config(self._config)

            self._switch_status = "ready"
            logger.info("Switched to model: %s", model_id)
            if self._is_inprocess():
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
                    language=self._config.language,
                    auto_detect_language=self._config.auto_language_detection,
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

        async def end_take(end: float) -> None:
            nonlocal take
            if take is not None:
                (token, start), take = take, None
                await loop.run_in_executor(None, self._end_take, token, start, end)

        try:
            # Connect to inference server BEFORE signalling readiness so the
            # WebSocket handshake doesn't race against hotkey release.
            await self._transcriber.connect()

            # Clear any stop event that arrived while we were connecting.
            # If the user already released the keys, skip this cycle. A phone recording
            # is not skipped: its audio was buffered while we connected, and the stop
            # event stays set so the wait below returns at once.
            if not remote and self._record_stop_event.is_set():
                self._record_stop_event.clear()
                logger.info("Hotkey released before recording could start, skipping")
                await self._transcriber.disconnect()
                await self._hide_os_control()
                if self._hotkey.mark == PASTE_LAST:
                    # A quick tap of the paste chord (or one queued behind a running
                    # cycle) needs no recording: paste the last text all the same.
                    await self._paste_last_text()
                elif self._hotkey.mark == POLISH:
                    # A quick tap of the polish chord: nothing was said, so the default
                    # instruction; there is no recording to save.
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
            await self._record_stop_event.wait()
            self._record_stop_event.clear()
            recording_stopped = True
            mark = None if remote else self._hotkey.mark

            # Stop audio (sends None sentinel, ending the stream task)
            audio.stop()
            audio_buffer = await stream_task

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

            if mark == PASTE_LAST:
                # The paste key was tapped during the hold: drop this recording and paste
                # the last text again instead of transcribing.
                await self._transcriber.disconnect()
                await self._paste_last_text()
                return

            # Finalize transcription
            result = await self._transcriber.finish()
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
            snippets = self._db.get_snippets()

            # A phone recording only wants its text back, never a PC action.
            if not remote:
                nexus_cmd = parse_nexus_command(raw_text)
                if nexus_cmd is not None and nexus_cmd.action == "flag":
                    self._flag_last_transcription()
                    await self._transcriber.disconnect()
                    return
                label = await self._run_command(raw_text, nexus_cmd, snippets)
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
                    snippets,
                    remote=remote,
                    extend_fn=None if remote else self._extend_dictation,
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
            record = self._db.save_transcription(
                text=processed.saved,
                language=self._language_of(raw_text),
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
            self._store_audio(audio_buffer, record.id)

        except Exception:
            logger.exception("Transcription cycle failed")
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

    def _extend_dictation(self, text: str) -> str:
        """The extensions' say on a dictation about to be typed (runs in the executor with the pipeline)."""
        registry = loaded_extensions()  # None while the packages still load: type without them, never wait
        if registry is None or not registry.extensions:
            return text
        return registry.dictation(text, self._active_app())

    def _language_of(self, spoken: str) -> str:
        return detect_language(spoken) if self._config.auto_language_detection else self._config.language

    def _save_command(self, recording: _Recording, label: str, spoken: str) -> None:
        """Save a dictation that ran a command instead of being typed, as ``label``."""
        record = self._db.save_transcription(
            text=label,
            language=self._language_of(spoken),
            duration_ms=recording.duration_ms,
            confidence=recording.confidence,
            model=self._transcriber.model,
            transcribe_ms=recording.transcribe_ms,
            app=COMMAND_APP,
        )
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
                    handled, window, f"[extension] {spoken}", spoken or selection, recording
                )
                return
        instruction, preset = resolve_instruction(spoken, self._db.get_polish_presets())
        if preset:
            logger.info("Polish: preset %r", preset)
        try:
            rewritten = await loop.run_in_executor(None, polish, selection, instruction, self._config.llm)
        except LlmError as exc:
            logger.warning("Polish: model at %s failed (%s); the selection stays as it was", self._config.llm.url, exc)
            self._cue(beep_error, ERROR)
            return
        await self._paste_over_selection(
            rewritten, window, f"[polish] {preset or spoken or 'default'}", spoken or selection, recording
        )

    async def _paste_over_selection(
        self, rewritten: str, window: int, label: str, spoken: str, recording: _Recording | None
    ) -> None:
        """Paste *rewritten* over the still-selected text, unless the focus left *window*; save *label*."""
        self._last_typed = rewritten
        if foreground_window() != window:
            logger.info("Polish: the focus moved to another window, not pasting (the paste key pastes it)")
            self._cue(beep_error, ERROR)
        else:
            # The selection is still selected, so pasting replaces it; Ctrl+Z in the app undoes it.
            try:
                await asyncio.get_running_loop().run_in_executor(None, inject_text, rewritten, 0)
            except ClipboardError as exc:
                logger.warning("Polish: could not paste the reply (%s); the paste key pastes it", exc)
                self._cue(beep_error, ERROR)
        if recording is not None:
            self._save_command(recording, label, spoken)

    def _flag_last_transcription(self) -> None:
        """ "nexus flag" - always available, flags the most recent transcription for review."""
        last_tid = self._db.get_last_transcription_id()
        if last_tid is None:
            logger.info("Nexus flag: no transcription to flag")
            return
        self._db.flag_transcription(last_tid)
        logger.info("Nexus flag: flagged transcription id=%d", last_tid)
        self._cue(beep_flag, CONFIRMED)

    async def _run_command(self, raw_text: str, nexus_cmd: NexusCommand | None, snippets: list[Snippet]) -> str | None:
        """Run the command *raw_text* is, if any. Returns the label to save it under, or None to type it.

        Checked in order: "nexus open <snippet>" (always available - the user's own
        snippet is the opt-in), "nexus assistant <text>" (when enabled; falls through
        to typing if nothing answers), nexus window commands (when enabled).
        """
        loop = asyncio.get_running_loop()
        spoken_target = parse_nexus_open(raw_text)
        target = resolve_open_target(spoken_target, snippets) if spoken_target is not None else None
        if target is not None:
            from . import window_manager

            try:
                opened = await loop.run_in_executor(None, window_manager.open_target, target)
            except OSError:
                logger.exception("Opening %r failed", Text(target))
                opened = False
            if not opened:
                self._cue(beep_error, ERROR)
            return f"[nexus] open {target}"

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

        if nexus_cmd is not None and self._config.os_commands.enabled:
            await loop.run_in_executor(None, execute_nexus_command, nexus_cmd, self._config.os_commands)
            logger.info("Nexus command: %s %s", nexus_cmd.action, nexus_cmd.app_name)
            return f"[nexus] {nexus_cmd.action} {nexus_cmd.app_name}"
        return None

    async def _stream_audio(self, audio: AudioCapture | RemoteAudioSource) -> bytearray:
        """Stream audio chunks from the mic or the phone to the transcription server, returning the full buffer."""
        buffer = bytearray()
        count = 0
        try:
            async for chunk in audio.chunks():
                await self._transcriber.send_audio_chunk(chunk)
                buffer.extend(chunk)
                count += 1
        except Exception:
            logger.exception("Error streaming audio")
        logger.info("Streamed %d audio chunks to server", count)
        return buffer

    def _open_dashboard(self) -> None:
        """Open the dashboard window."""
        open_dashboard(self._db, self._config, app=self)

    def _toggle_language(self) -> None:
        """Toggle between English and German."""
        self._config.language = "de" if self._config.language == "en" else "en"
        save_config(self._config)
        logger.info("Language switched to: %s", self._config.language)

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
        if self._is_inprocess():
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

    async def _preload_transcriber(self) -> None:
        """Warm up an in-process transcriber so the first hotkey press is snappy."""
        try:
            logger.info("Preloading in-process transcriber model (first run may download)")
            await self._transcriber.connect()
            await self._transcriber.disconnect()
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
        if self._remote_mic is not None:
            await self._remote_mic.close()
        if self._mic_guard is not None:
            self._mic_guard.release()
        await self._transcriber.disconnect()

    def run(self) -> None:
        """Start the application."""
        asyncio.run(self._run())
