"""Main application orchestrator — wires hotkey, audio, transcriber, and injector."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import docker_ctl
from .audio import AudioCapture
from .config import MODEL_REGISTRY, Config, resolve_device, save_config
from .dashboard import open_dashboard
from .db import Database
from .feedback import beep_flag, beep_start, beep_stop
from .hotkey import HotkeyListener
from .injector import inject_text
from .lang_detect import detect_language
from .os_commands import execute_nexus_command, parse_nexus_command
from .transcriber import create_transcriber
from .tray import SystemTray
from .voice_commands import process_voice_commands

_CPU_FALLBACK_MODEL = "whisper-large-v3-turbo"

logger = logging.getLogger(__name__)


class NexusVoxApp:
    """Orchestrates the push-to-talk transcription pipeline."""

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
        info = MODEL_REGISTRY.get(config.inference.model, {})
        if self._device == "cpu" and info.get("requires_gpu"):
            logger.warning(
                "Configured model %s requires a GPU but device is CPU. Falling back to %s for this session.",
                config.inference.model,
                _CPU_FALLBACK_MODEL,
            )
            config.inference.model = _CPU_FALLBACK_MODEL

        # Components
        self._audio = AudioCapture(config.audio)
        self._transcriber = create_transcriber(
            config.inference,
            device=self._device,
            language=config.language,
            auto_detect_language=config.auto_language_detection,
        )
        self._db = Database(config.database)

        self._hotkey = HotkeyListener(
            config=config.hotkey,
            on_activate=self._on_hotkey_activate,
            on_deactivate=self._on_hotkey_deactivate,
        )

        self._tray = SystemTray(
            on_quit=self._on_quit,
            on_toggle_language=self._toggle_language,
            get_language=lambda: self._config.language,
            on_open_dashboard=self._open_dashboard,
        )

        # Signals between hotkey thread and asyncio loop
        self._recording = False
        self._record_start_event: asyncio.Event | None = None
        self._record_stop_event: asyncio.Event | None = None

        # Model switch state
        self._switch_status: str = "idle"
        self._switch_error: str | None = None

    def _on_hotkey_activate(self) -> None:
        """Called from hotkey thread when push-to-talk starts."""
        if self._loop is not None and self._record_start_event is not None:
            self._loop.call_soon_threadsafe(self._record_start_event.set)

    def _on_hotkey_deactivate(self) -> None:
        """Called from hotkey thread when push-to-talk ends."""
        if self._loop is not None and self._record_stop_event is not None:
            self._loop.call_soon_threadsafe(self._record_stop_event.set)

    def get_switch_status(self) -> dict:
        """Return the current model-switch status for the dashboard."""
        return {"status": self._switch_status, "error": self._switch_error}

    async def switch_model(self, model_id: str) -> None:
        """Switch to a different transcription model.

        In-process (Whisper-on-CPU) switches skip Docker entirely; GPU-backed
        switches stop the old container, start the new one, and wait for health.
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

        new_info = MODEL_REGISTRY[model_id]
        old_info = MODEL_REGISTRY[old_model]
        loop = asyncio.get_event_loop()

        new_inprocess = bool(new_info.get("inprocess_supported")) and self._device == "cpu"
        old_was_docker = self._transcriber.needs_docker

        try:
            self._switch_status = "stopping"
            await self._transcriber.disconnect()

            if old_was_docker:
                await loop.run_in_executor(None, docker_ctl.stop_profile, old_info["docker_profile"])

            if not new_inprocess:
                self._switch_status = "starting"
                await loop.run_in_executor(None, docker_ctl.start_profile, new_info["docker_profile"])
                self._switch_status = "waiting"
                healthy = await loop.run_in_executor(None, docker_ctl.wait_for_healthy, new_info["health_url"])
                if not healthy:
                    raise RuntimeError(f"Container for {model_id} did not become healthy")

            self._config.inference.model = model_id
            if not new_inprocess:
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

            async def _reset_status():
                await asyncio.sleep(5)
                self._switch_status = "idle"
                self._switch_error = None

            asyncio.ensure_future(_reset_status())

        except Exception as exc:
            self._switch_status = "error"
            self._switch_error = str(exc)
            logger.exception("Model switch failed")
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

    async def _transcription_cycle(self) -> None:
        """Run one full transcription cycle: connect, record, transcribe, inject."""
        try:
            # Connect to inference server BEFORE signalling readiness so the
            # WebSocket handshake doesn't race against hotkey release.
            await self._transcriber.connect()

            # Clear any stop event that arrived while we were connecting.
            # If the user already released the keys, skip this cycle.
            if self._record_stop_event.is_set():
                self._record_stop_event.clear()
                logger.info("Hotkey released before recording could start, skipping")
                await self._transcriber.disconnect()
                return

            # Now we are ready — start recording
            self._tray.set_active(True)
            threading.Thread(target=beep_start, daemon=True).start()
            logger.info("Recording started")
            start_time = time.monotonic()

            # Start audio capture
            self._audio.start()

            # Stream audio chunks until recording stops
            stream_task = asyncio.create_task(self._stream_audio())

            # Wait for hotkey release
            await self._record_stop_event.wait()
            self._record_stop_event.clear()

            # Stop audio (sends None sentinel, ending the stream task)
            self._audio.stop()
            audio_buffer = await stream_task

            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info("Recording stopped (duration=%dms)", duration_ms)

            self._tray.set_active(False)
            threading.Thread(target=beep_stop, daemon=True).start()

            # Finalize transcription
            result = await self._transcriber.finish()

            if result.text.strip():
                raw_text = result.text.strip()
                loop = asyncio.get_event_loop()

                # Check for nexus commands before any text processing
                nexus_cmd = parse_nexus_command(raw_text)

                if nexus_cmd is not None and nexus_cmd.action == "flag":
                    # "nexus flag" — always available, flags most recent transcription
                    last_tid = self._db.get_last_transcription_id()
                    if last_tid is not None:
                        self._db.flag_transcription(last_tid)
                        logger.info("Nexus flag: flagged transcription id=%d", last_tid)
                        threading.Thread(target=beep_flag, daemon=True).start()
                    else:
                        logger.info("Nexus flag: no transcription to flag")
                    await self._transcriber.disconnect()
                    return

                if nexus_cmd is not None and self._config.os_commands.enabled:
                    # Window management nexus commands — only when os_commands enabled
                    await loop.run_in_executor(
                        None,
                        execute_nexus_command,
                        nexus_cmd,
                        self._config.os_commands,
                    )
                    language = (
                        detect_language(raw_text) if self._config.auto_language_detection else self._config.language
                    )
                    record = self._db.save_transcription(
                        text=f"[nexus] {nexus_cmd.action} {nexus_cmd.app_name}",
                        language=language,
                        duration_ms=duration_ms,
                        confidence=result.confidence,
                        model=self._transcriber.model,
                    )
                    logger.info("Nexus command: %s %s", nexus_cmd.action, nexus_cmd.app_name)
                    audio_path = self._save_audio_wav(audio_buffer, record.id)
                    self._db.update_audio_path(record.id, audio_path)
                    await self._transcriber.disconnect()
                    return

                # Apply voice commands (new line, tab, all caps, symbols, etc.)
                if self._config.voice_commands.enabled:
                    active_symbols = (
                        frozenset()
                        if self._config.voice_commands.bypass_symbols
                        else frozenset(self._config.voice_commands.symbols)
                    )
                    processed_text = process_voice_commands(
                        raw_text,
                        active_symbols,
                        self._config.voice_commands.numbers_as_digits,
                    )
                else:
                    processed_text = raw_text

                delay = self._config.injection_delay_ms
                await loop.run_in_executor(
                    None,
                    inject_text,
                    processed_text,
                    delay,
                )
                language = detect_language(raw_text) if self._config.auto_language_detection else self._config.language
                record = self._db.save_transcription(
                    text=processed_text,
                    language=language,
                    duration_ms=duration_ms,
                    confidence=result.confidence,
                    model=self._transcriber.model,
                )
                logger.info("Injected: %s", processed_text)

                audio_path = self._save_audio_wav(audio_buffer, record.id)
                self._db.update_audio_path(record.id, audio_path)
            else:
                logger.info("Empty transcription, nothing to inject")

        except Exception:
            logger.exception("Transcription cycle failed")
            self._tray.set_active(False)
            await self._transcriber.disconnect()

    async def _stream_audio(self) -> bytearray:
        """Stream audio chunks from microphone to the transcription server, returning the full buffer."""
        buffer = bytearray()
        count = 0
        try:
            async for chunk in self._audio.chunks():
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
        logger.info("Language switched to: %s", self._config.language)

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
        self._tray.start()

        logger.info(
            "NexusVox started. Hold %s to talk.",
            "+".join(m.capitalize() for m in self._config.hotkey.modifiers),
        )

        # For in-process transcribers (Whisper on CPU), eagerly load the model
        # so the first hotkey press doesn't pay the full load + download latency.
        if not self._transcriber.needs_docker:
            asyncio.create_task(self._preload_transcriber())

        # Main loop: wait for hotkey press, run transcription cycle
        while self._running:
            await self._record_start_event.wait()
            self._record_start_event.clear()

            if not self._running:
                break

            await self._transcription_cycle()

        # Cleanup
        await self._shutdown()

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
        self._tray.stop()
        self._audio.stop()
        await self._transcriber.disconnect()

    def run(self) -> None:
        """Start the application."""
        asyncio.run(self._run())
