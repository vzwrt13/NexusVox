"""Dashboard API: file transcriptions, from the upload to the stored row and its exports."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path

from ..dictation.lang_detect import stored_language
from ..extensions import HOOK_TIMEOUT_S, file_transcript_from_record
from ..file_cleanup import clean_transcript
from ..llm import config_for
from ..transcription.diarization import speaker_turns
from ..transcription.exports import NO_TIMING_HINT, SUBTITLE_TARGET_IDS
from ..transcription.file_transcribe import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, convert_to_wav, transcribe_file
from ..transcription.speaker_fallback import speaker_detection_job
from .api_base import DashboardState, logger
from .file_jobs import FileJob

# The longest title a file transcription can carry (the column is VARCHAR(255)).
MAX_TITLE_LENGTH = 255
# Names Windows will not create as a file, with or without an extension.
_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_stem(name: str) -> str:
    """`name` as Windows accepts it for a file; empty when nothing of it is left."""
    stem = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")
    return "_" + stem if stem.split(".")[0].upper() in _RESERVED_FILENAMES else stem


def _export_stem(record) -> str:
    """The download name (without extension) of an export: the title when set, else the file or folder name."""
    return _safe_stem(record.title or "") or _safe_stem(Path(record.original_filename).stem) or "transcript"


class FilesMixin(DashboardState):
    """Uploads, the jobs that transcribe them, and the rows uploads and meetings share."""

    def start_file_transcription(
        self, file_bytes: bytes, filename: str, *, diarize: bool = False, cleanup_level: str = ""
    ) -> dict:
        """Check an uploaded file and transcribe it in the background; `{"ok": True, "job_id"}` at once.

        A `cleanup_level` of `light` or `medium` also runs the transcript through the [llm] model;
        anything else is no cleanup.

        Progress and the outcome are read with `get_file_job` / `get_current_file_job`.
        """
        error = self._check_upload(file_bytes, filename)
        if error:
            return {"ok": False, "error": error}
        job = self._file_jobs.start(
            filename,
            lambda job: self.upload_and_transcribe(
                file_bytes, filename, diarize=diarize, cleanup_level=cleanup_level, job=job
            ),
        )
        if job is None:
            return {"ok": False, "busy": True, "error": "Another file is still being transcribed"}
        return {"ok": True, "job_id": job.id}

    def get_file_job(self, job_id: str) -> dict | None:
        return self._file_jobs.get(job_id)

    def get_current_file_job(self) -> dict | None:
        """The running file job, or the latest one whose ending no client has read yet."""
        return self._file_jobs.current()

    def _check_upload(self, file_bytes: bytes, filename: str) -> str:
        """Why an uploaded file cannot be transcribed, or an empty string."""
        if self._db is None:
            return "DB not available"
        from ..transcription.file_transcribe import _get_extension

        ext = _get_extension(filename)
        if ext not in ALLOWED_EXTENSIONS:
            return f"Unsupported format: {ext}"
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            return f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        return ""

    def upload_and_transcribe(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        diarize: bool = False,
        cleanup_level: str = "",
        job: FileJob | None = None,
    ) -> dict:
        """Convert an uploaded audio file, transcribe it, and save to DB (blocking); the file is already checked.

        With `diarize`, the saved and returned text is labelled by speaker when the model supports it.
        With a `cleanup_level`, the transcript is also cleaned up by the [llm] model; that text is saved
        beside the transcript, never in place of it. `job` receives the stage as the work moves on.
        """
        started = time.monotonic()
        progress = job.update if job else None
        logger.info("File transcription started: %d KB", len(file_bytes) // 1024)
        if progress:
            progress("converting")

        try:
            wav_chunks, duration_ms = convert_to_wav(file_bytes, filename)
        except Exception as exc:
            logger.error("Audio conversion failed for %s: %s", filename, exc)
            return {"ok": False, "error": f"Audio conversion failed: {exc}"}

        # A model without speaker detection runs this upload on Parakeet (see `speaker_fallback`). The container, if
        # the job started it, is stopped as soon as the transcription is done: the cleanup below may need the VRAM.
        try:
            with speaker_detection_job(
                self._config.inference, wanted=diarize, what="upload", active_model=lambda: self._config.inference.model
            ) as speakers:
                result = self._transcribe_wav_chunks(
                    wav_chunks, word_timestamps=True, diarize=diarize, on_progress=progress, inference=speakers.config
                )
        except Exception as exc:
            logger.error("Transcription failed for %s: %s", filename, exc)
            return {"ok": False, "error": f"Transcription failed: {exc}"}

        notes = [speakers.notice, *result.notes] if speakers.notice else list(result.notes)
        cleaned_text = None
        if cleanup_level and result.text:
            try:
                cleaned = clean_transcript(
                    result.text, cleanup_level, config_for(self._config.llm, "file_cleanup"), progress
                )
            except Exception as exc:  # clean_transcript reports model failures itself; this is a bug
                logger.exception("Cleaning up the transcript of %s failed: %s", filename, exc)
                notes.append("Text cleanup failed; the transcript is saved as transcribed.")
            else:
                if cleaned.note:
                    notes.append(cleaned.note)
                if cleaned.text != result.text:
                    cleaned_text = cleaned.text

        return self._store_file_transcription(
            filename,
            notes,
            progress,
            text=result.text,
            duration_ms=duration_ms,
            confidence=result.confidence,
            words=result.words,
            model=speakers.config.model,
            cleaned_text=cleaned_text,
            cleanup_level=cleanup_level,
            processing_ms=int((time.monotonic() - started) * 1000),
        )

    def _store_file_transcription(self, filename: str, notes: list[str], progress, **fields) -> dict:
        """Save a finished file transcription, run the extension hook and build the job result (blocking).

        `fields` go to `Database.save_file_transcription`; the language is detected from the text.
        """
        if progress:
            progress("saving")
        try:
            record = self._db.save_file_transcription(
                language=stored_language(fields.get("text", ""), strip_labels=True),
                original_filename=filename,
                **{"model": self._config.inference.model, **fields},
            )
        except Exception as exc:
            logger.error("Saving the transcription of %s failed: %s", filename, exc)
            return {"ok": False, "error": f"Saving the transcript failed: {exc}"}
        logger.info(
            "Transcription saved: id=%s, kind=%s, %.1f s of audio",
            record.id,
            record.kind,
            (record.duration_ms or 0) / 1000,
        )

        extension_results: dict = {}
        try:
            extension_results, extension_notes = self.extensions.file_transcribed(file_transcript_from_record(record))
            notes.extend(extension_notes)
        except Exception as exc:  # the transcript is stored; extensions are an extra
            logger.exception("Extensions could not process the transcription of %s: %s", filename, exc)
            notes.append(f"Extensions could not process this transcript: {exc}")

        return {
            "ok": True,
            "notes": notes,
            "extensions": extension_results,
            "id": record.id,
            "kind": record.kind,
            "text": record.text,
            "cleaned_text": record.cleaned_text,
            "cleanup_level": record.cleanup_level,
            "language": record.language,
            "duration_ms": record.duration_ms,
            "model": record.model,
            "original_filename": record.original_filename,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

    def delete_file_transcription(self, tid: int) -> dict:
        """Delete an upload or a meeting with its audio: an upload's stored file, a meeting's whole folder
        (whatever is left of `mic.wav`, `system.wav`, `meeting.wav` and `meeting.json`).
        A file that is already gone does not stop it."""
        record = self._db.get_file_transcription(tid) if self._db else None
        if record is None:
            return {"ok": False, "error": "Unknown file transcription"}
        try:
            if (record.kind or "upload") == "meeting":
                self._remove_meeting_files(record)
            elif record.audio_path:
                path = (self._db_dir() / record.audio_path).resolve()
                if path.is_relative_to(self._db_dir()):
                    path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Deleting the audio of file transcription %s failed: %s", tid, exc)
            return {"ok": False, "error": f"Could not delete the audio files: {exc}"}
        self._db.delete_file_transcription(tid)
        return {"ok": True}

    def get_file_audio_path(self, tid: int) -> str | None:
        """Absolute path of the playable audio kept for a file transcription (a meeting's mix), if any."""
        record = self._db.get_file_transcription(tid) if self._db else None
        if record is None or not record.audio_path:
            return None
        path = self._db_dir() / record.audio_path
        return str(path) if path.is_file() else None

    def _transcribe_wav_chunks(
        self,
        wav_chunks: list[bytes],
        *,
        word_timestamps: bool = False,
        diarize: bool = False,
        on_progress: Callable[..., None] | None = None,
        inference=None,
    ):
        """Transcribe WAV chunks with the current model, or with `inference` when a job brings its own (blocking)."""
        return asyncio.run(
            transcribe_file(
                wav_chunks,
                inference or self._config.inference,
                word_timestamps=word_timestamps,
                diarize=diarize,
                on_progress=on_progress,
            )
        )

    def rename_file_transcription(self, tid: int, title: object) -> dict:
        """Give an upload or meeting a title (trimmed, at most 255 characters); an empty or null one clears it.

        A failure carries the HTTP `status` that fits it."""
        if title is not None and not isinstance(title, str):
            return {"ok": False, "status": 400, "error": "title must be a string or null"}
        title = (title or "").strip()
        if len(title) > MAX_TITLE_LENGTH:
            return {"ok": False, "status": 400, "error": f"title is longer than {MAX_TITLE_LENGTH} characters"}
        record = self._db.set_file_transcription_title(tid, title or None) if self._db else None
        if record is None:
            return {"ok": False, "status": 404, "error": "Unknown file transcription"}
        return {"ok": True, "id": record.id, "title": record.title}

    def get_file_transcriptions(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        """Return file transcription records; `kind` (`upload` / `meeting`) narrows them."""
        if self._db is None:
            return []
        rows = self._db.get_file_transcriptions(limit, kind)
        # One budget for the whole list, so a slow extension delays it by one timeout, not one per row.
        deadline = time.monotonic() + HOOK_TIMEOUT_S
        out = []
        for r in rows:
            text, cleaned_text = self._displayed(r, deadline)
            out.append(
                {
                    "id": r.id,
                    "text": text,
                    "cleaned_text": cleaned_text,
                    "cleanup_level": r.cleanup_level,
                    "language": r.language,
                    "duration_ms": r.duration_ms,
                    "model": r.model,
                    "original_filename": r.original_filename,
                    "title": r.title,
                    "kind": r.kind or "upload",
                    "has_audio": bool(r.audio_path),
                    "confidence": r.confidence,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "exports_unavailable": self._unavailable_exports(r, deadline),
                    "actions_unavailable": self.extensions.unavailable_actions(r.id, deadline=deadline),
                }
            )
        return out

    def get_speaker_turns(self, tid: int) -> list[dict] | None:
        """`[{speaker, start, end}]` (seconds) for each speaker paragraph of a stored row, in text order; empty when
        the row has no speaker-labelled word timing. None for an unknown id. The Recordings tab puts the start
        next to each paragraph and sums the talk time per speaker."""
        record = self._db.get_file_transcription(tid) if self._db else None
        if record is None:
            return None
        words = list(file_transcript_from_record(record).words)
        return [
            {"speaker": speaker, "start": round(start, 2), "end": round(end, 2)}
            for speaker, start, end in speaker_turns(words)
        ]

    def _displayed(self, record, deadline: float) -> tuple[str, str | None]:
        """A row's `text` and `cleaned_text` as the extensions' `display_text` hook shows them, in one call."""
        if not self.extensions.extensions:  # nothing to ask: skip building the hook view (parses the word timings)
            return record.text, record.cleaned_text
        texts = [record.text, *([record.cleaned_text] if record.cleaned_text else [])]
        shown = self.extensions.display_texts(file_transcript_from_record(record), texts, deadline=deadline)
        return shown[0], shown[1] if len(shown) > 1 else record.cleaned_text

    def _unavailable_exports(self, record, deadline: float | None = None) -> dict[str, str | dict]:
        """`{target id: reason}` for the export targets that cannot export this row: the extensions' own checks,
        plus the built-in subtitle targets when no word timing is stored."""
        unavailable = self.extensions.unavailable_exports(record.id, deadline=deadline)
        if not record.words or record.words.strip() == "[]":
            for target_id in SUBTITLE_TARGET_IDS:
                if self.extensions.export_target(target_id) is not None:
                    unavailable.setdefault(target_id, NO_TIMING_HINT)
        return unavailable

    def export_file_transcription(self, transcription_id: int, target_id: str) -> dict:
        """Export a stored file transcription with an export target (built in or an extension's).

        `{"ok": True, "filename", "mimetype", "data": bytes}`; `status` says which HTTP error fits a failure.
        """
        target = self.extensions.export_target(target_id)
        if target is None:
            return {"ok": False, "status": 404, "error": "Unknown export target"}
        record = self._db.get_file_transcription(transcription_id) if self._db else None
        if record is None:
            return {"ok": False, "status": 404, "error": "Unknown file transcription"}
        try:
            data = target.export(file_transcript_from_record(record))
            if isinstance(data, str):
                data = data.encode("utf-8")
            if not isinstance(data, bytes):
                raise TypeError(f"expected bytes or str, got {type(data).__name__}")
        except Exception as exc:
            logger.exception("Export target %s failed on file transcription %s", target_id, transcription_id)
            return {"ok": False, "status": 500, "error": f"Export failed: {exc}"}
        stem = _export_stem(record)
        return {"ok": True, "filename": stem + target.extension, "mimetype": target.mimetype, "data": data}
