"""Dashboard API: flagged recordings and the Review tab."""

from __future__ import annotations

from pathlib import Path

from ..dictation.dictionary import suggest_entries
from ..transcription.file_transcribe import convert_to_wav
from . import analytics
from .api_base import DashboardState, logger


class ReviewMixin(DashboardState):
    """Flagged transcriptions, corrections and the review queue."""

    # ---- Flagged / Corrections ---------------------------------------------

    def get_flagged_transcriptions(self) -> list[dict]:
        return analytics.get_flagged_transcriptions(self._sf)

    def _recording_audio_path(self, tid: int) -> Path | None:
        """Where a transcription's audio file is meant to be, whether or not it still exists."""
        if self._db is None:
            return None
        from ..storage.models import Transcription

        with self._sf() as session:
            row = session.get(Transcription, tid)
            return self._db_dir() / row.audio_path if row and row.audio_path else None

    def get_audio_file_path(self, tid: int) -> str | None:
        """Resolve the absolute path to a transcription's audio file, None when it has none or the file is gone."""
        path = self._recording_audio_path(tid)
        return str(path) if path is not None and path.is_file() else None

    def retry_transcription(self, tid: int) -> dict:
        """Transcribe a recording's stored WAV again with the current model. Saves nothing."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        stored = self._recording_audio_path(tid)
        if stored is None:
            return {"ok": False, "error": "No stored audio for this recording - turn on Store Audio Recordings"}
        if not stored.is_file():
            return {"ok": False, "error": "The stored audio file for this recording is missing"}
        path = str(stored)
        try:
            with convert_to_wav(Path(path), "recording.wav") as wav_chunks:
                result = self._transcribe_wav_chunks(wav_chunks)
        except Exception as exc:
            logger.error("Retry failed for transcription %d: %s", tid, exc)
            return {"ok": False, "error": f"Transcription failed: {exc}"}
        if result.notes:
            return {"ok": False, "error": "Transcription incomplete: " + " ".join(result.notes)}
        return {"ok": True, "text": result.text, "model": self._config.inference.model}

    def update_correction(self, tid: int, corrected_text: str) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        ok = self._db.update_correction(tid, corrected_text)
        return {"ok": ok}

    # ---- Review ------------------------------------------------------------

    def get_unreviewed_transcriptions(self, *, before_id: int | None = None) -> dict:
        return analytics.get_unreviewed_transcriptions(self._sf, before_id=before_id)

    def submit_review(self, tid: int, is_correct: bool, corrected_text: str | None = None) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        original = self._db.get_transcription_text(tid)
        ok = self._db.submit_review(tid, is_correct, corrected_text)
        suggestions: list[dict] = []
        if ok and not is_correct and corrected_text and original:
            suggestions = [
                {"wrong": e.wrong, "right": e.right}
                for e in suggest_entries(original, corrected_text, self._db.get_dictionary_entries())
            ]
        return {"ok": ok, "suggestions": suggestions}
