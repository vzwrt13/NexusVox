"""What is kept of a dictation: statistics, the transcript row of a command and the WAV file."""

from __future__ import annotations

import logging
import os
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .lang_detect import stored_language
from .models import COMMAND_APP

# All parts of `NexusVoxApp` log under the one name `app.py` has.
logger = logging.getLogger("nexusvox.app")


@dataclass(frozen=True)
class _Recording:
    """One finished recording, as saved with whatever it turned into."""

    audio: bytearray
    duration_ms: int
    confidence: float | None
    transcribe_ms: int


class RecordKeeping:
    """The storage part of `NexusVoxApp`."""

    @staticmethod
    def _store_stat(write: Callable[..., None], *args) -> None:
        try:
            write(*args)
        except Exception:
            logger.exception("Could not store a statistic")

    def get_learn_status(self) -> dict:
        """How the pastes since start ended for learning from edits, for the dashboard."""
        return self._edit_watcher.status()

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
