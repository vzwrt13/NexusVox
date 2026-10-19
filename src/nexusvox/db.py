"""Database session management."""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker

from .config import DatabaseConfig
from .dictionary import DictionaryEntry
from .models import (
    Base,
    DictionaryEntryRow,
    DictionarySuggestionRow,
    FileTranscription,
    PolishPresetRow,
    SnippetRow,
    Transcription,
    utc_now,
)
from .polish_presets import MAX_PRESET_NAME_WORDS, PolishPreset
from .snippets import Snippet, SnippetOption

if TYPE_CHECKING:
    from .transcriber import Word

logger = logging.getLogger(__name__)


def _word_json(word: Word) -> dict:
    """A word as stored in ``file_transcriptions.words``; ``speaker`` only once diarization set it."""
    data: dict = {"word": word.word, "start": word.start, "end": word.end}
    if word.speaker is not None:
        data["speaker"] = word.speaker
    return data


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        self._engine = create_engine(f"sqlite:///{config.path}")
        Base.metadata.create_all(self._engine)
        self._migrate()
        self._session_factory = sessionmaker(bind=self._engine)

    def _migrate(self) -> None:
        """Add columns that may be missing from older databases."""
        migrations = [
            "ALTER TABLE transcriptions ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE transcriptions ADD COLUMN corrected_text TEXT",
            "ALTER TABLE transcriptions ADD COLUMN model VARCHAR(100)",
            "ALTER TABLE transcriptions ADD COLUMN audio_path VARCHAR(500)",
            "ALTER TABLE transcriptions ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE transcriptions ADD COLUMN translated INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE transcriptions ADD COLUMN translate_source VARCHAR(5)",
            "ALTER TABLE transcriptions ADD COLUMN translate_ms INTEGER",
            "ALTER TABLE transcriptions ADD COLUMN translate_error VARCHAR(200)",
            "ALTER TABLE transcriptions ADD COLUMN transcribe_ms INTEGER",
            "ALTER TABLE transcriptions ADD COLUMN latency_ms INTEGER",
            "ALTER TABLE snippets ADD COLUMN options TEXT",
            "ALTER TABLE file_transcriptions ADD COLUMN words TEXT",
            "ALTER TABLE file_transcriptions ADD COLUMN cleaned_text TEXT",
            "ALTER TABLE file_transcriptions ADD COLUMN cleanup_level VARCHAR(10)",
        ]
        with self._engine.connect() as conn:
            for sql in migrations:
                try:
                    conn.execute(text(sql))
                    conn.commit()
                except Exception:
                    conn.rollback()

    def save_transcription(
        self,
        text: str,
        language: str,
        duration_ms: int,
        confidence: float | None = None,
        model: str | None = None,
        audio_path: str | None = None,
        translated: bool = False,
        translate_source: str | None = None,
        translate_ms: int | None = None,
        translate_error: str | None = None,
        transcribe_ms: int | None = None,
        latency_ms: int | None = None,
    ) -> Transcription:
        """Save a transcription record to the database."""
        with self._session_factory() as session:
            record = Transcription(
                text=text,
                language=language,
                duration_ms=duration_ms,
                created_at=utc_now(),
                confidence=confidence,
                model=model,
                audio_path=audio_path,
                translated=int(translated),
                translate_source=translate_source,
                translate_ms=translate_ms,
                translate_error=translate_error,
                transcribe_ms=transcribe_ms,
                latency_ms=latency_ms,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_recent(self, limit: int = 20) -> list[Transcription]:
        """Get the most recent transcriptions."""
        with self._session_factory() as session:
            return (
                session.query(Transcription)
                .order_by(Transcription.created_at.desc(), Transcription.id.desc())
                .limit(limit)
                .all()
            )

    def get_transcription_text(self, tid: int) -> str | None:
        """Return the stored text of a transcription, or None when it does not exist."""
        with self._session_factory() as session:
            row = session.get(Transcription, tid)
            return row.text if row is not None else None

    def flag_transcription(self, tid: int) -> bool:
        """Mark a transcription as incorrectly recognized."""
        with self._session_factory() as session:
            row = session.get(Transcription, tid)
            if row is None:
                return False
            row.flagged = 1
            session.commit()
            return True

    def update_correction(self, tid: int, corrected_text: str) -> bool:
        """Store the user-provided correct text for a transcription."""
        with self._session_factory() as session:
            row = session.get(Transcription, tid)
            if row is None:
                return False
            row.corrected_text = corrected_text
            session.commit()
            return True

    def get_flagged(self, limit: int = 50) -> list[Transcription]:
        """Get flagged transcriptions, newest first."""
        with self._session_factory() as session:
            return (
                session.query(Transcription)
                .filter(Transcription.flagged == 1)
                .order_by(Transcription.created_at.desc(), Transcription.id.desc())
                .limit(limit)
                .all()
            )

    def update_audio_path(self, tid: int, audio_path: str) -> None:
        """Store the audio file path for a transcription."""
        with self._session_factory() as session:
            row = session.get(Transcription, tid)
            if row:
                row.audio_path = audio_path
                session.commit()

    def get_last_transcription_id(self) -> int | None:
        """Return the id of the most recent transcription."""
        with self._session_factory() as session:
            row = (
                session.query(Transcription.id)
                .order_by(Transcription.created_at.desc(), Transcription.id.desc())
                .first()
            )
            return row[0] if row else None

    # ---- Review -------------------------------------------------------------

    def get_unreviewed(self, limit: int = 50) -> list[Transcription]:
        """Get unreviewed transcriptions that have audio, newest first."""
        with self._session_factory() as session:
            return (
                session.query(Transcription)
                .filter(Transcription.reviewed == 0, Transcription.audio_path.isnot(None))
                .order_by(Transcription.created_at.desc(), Transcription.id.desc())
                .limit(limit)
                .all()
            )

    def submit_review(self, tid: int, is_correct: bool, corrected_text: str | None = None) -> bool:
        """Record a manual review for a transcription."""
        with self._session_factory() as session:
            row = session.get(Transcription, tid)
            if row is None:
                return False
            row.reviewed = 1 if is_correct else 2
            if not is_correct and corrected_text is not None:
                row.corrected_text = corrected_text
            session.commit()
            return True

    # ---- Correction dictionary ----------------------------------------------

    def list_dictionary(self) -> list[dict]:
        """All dictionary entries, newest first, as plain dicts."""
        with self._session_factory() as session:
            rows = session.query(DictionaryEntryRow).order_by(DictionaryEntryRow.id.desc()).all()
            return [{"id": r.id, "wrong": r.wrong, "right": r.right} for r in rows]

    def get_dictionary_entries(self) -> list[DictionaryEntry]:
        """Dictionary entries ready for ``apply_dictionary``."""
        with self._session_factory() as session:
            rows = session.query(DictionaryEntryRow).all()
            return [DictionaryEntry(wrong=r.wrong, right=r.right) for r in rows]

    def add_dictionary_entry(self, wrong: str, right: str) -> dict | None:
        """Insert or update an entry. Returns the entry, or None when either side is blank."""
        wrong = " ".join(wrong.split())
        right = right.strip()
        if not wrong or not right:
            return None
        with self._session_factory() as session:
            row = (
                session.query(DictionaryEntryRow).filter(func.lower(DictionaryEntryRow.wrong) == wrong.lower()).first()
            )
            if row is None:
                row = DictionaryEntryRow(wrong=wrong, right=right)
                session.add(row)
            else:
                row.wrong = wrong
                row.right = right
            session.commit()
            return {"id": row.id, "wrong": row.wrong, "right": row.right}

    def delete_dictionary_entry(self, entry_id: int) -> bool:
        with self._session_factory() as session:
            row = session.get(DictionaryEntryRow, entry_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # ---- Dictionary suggestions (learned from edits) ------------------------

    def add_dictionary_suggestions(self, entries: list[DictionaryEntry]) -> int:
        """Queue suggestions, skipping ones already pending or in the dictionary. Returns how many were added."""
        added = 0
        with self._session_factory() as session:
            for entry in entries:
                wrong = " ".join(entry.wrong.split())
                right = entry.right.strip()
                if not wrong or not right:
                    continue
                known = (
                    session.query(DictionaryEntryRow)
                    .filter(func.lower(DictionaryEntryRow.wrong) == wrong.lower())
                    .first()
                    or session.query(DictionarySuggestionRow)
                    .filter(
                        func.lower(DictionarySuggestionRow.wrong) == wrong.lower(),
                        DictionarySuggestionRow.right == right,
                    )
                    .first()
                )
                if known is None:
                    session.add(DictionarySuggestionRow(wrong=wrong, right=right))
                    added += 1
            session.commit()
        return added

    def list_dictionary_suggestions(self) -> list[dict]:
        """Pending suggestions, newest first."""
        with self._session_factory() as session:
            rows = session.query(DictionarySuggestionRow).order_by(DictionarySuggestionRow.id.desc()).all()
            return [{"id": r.id, "wrong": r.wrong, "right": r.right} for r in rows]

    def accept_dictionary_suggestion(self, suggestion_id: int) -> dict | None:
        """Turn a suggestion into a dictionary entry. Returns the entry, or None when it is gone.

        Other pending suggestions for the same `wrong` are dropped too: accepting one of
        them later would silently overwrite the entry just added.
        """
        with self._session_factory() as session:
            row = session.get(DictionarySuggestionRow, suggestion_id)
            if row is None:
                return None
            wrong, right = row.wrong, row.right
        entry = self.add_dictionary_entry(wrong, right)
        if entry is not None:
            with self._session_factory() as session:
                session.query(DictionarySuggestionRow).filter(
                    func.lower(DictionarySuggestionRow.wrong) == wrong.lower()
                ).delete(synchronize_session=False)
                session.commit()
        return entry

    def delete_dictionary_suggestion(self, suggestion_id: int) -> bool:
        with self._session_factory() as session:
            row = session.get(DictionarySuggestionRow, suggestion_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # ---- Snippets -----------------------------------------------------------

    @staticmethod
    def _snippet(row: SnippetRow) -> Snippet:
        options = tuple(SnippetOption(name, text) for name, text in json.loads(row.options)) if row.options else ()
        return Snippet(trigger=row.trigger, expansion=row.expansion, options=options)

    @classmethod
    def _snippet_dict(cls, row: SnippetRow) -> dict:
        snippet = cls._snippet(row)
        return {
            "id": row.id,
            "trigger": snippet.trigger,
            "expansion": snippet.expansion,
            "options": [o._asdict() for o in snippet.options],
        }

    @staticmethod
    def _clean_snippet(snippet: Snippet) -> tuple[str, str | None] | None:
        """(normalised trigger, options JSON), or None when the snippet has no trigger or nothing to insert."""
        trigger = " ".join(snippet.trigger.split())
        if not trigger or not (snippet.expansion.strip() or snippet.options):
            return None
        options = json.dumps([list(o) for o in snippet.options], ensure_ascii=False) if snippet.options else None
        return trigger, options

    def list_snippets(self) -> list[dict]:
        """All snippets, sorted by trigger, as plain dicts (``options`` as ``{name, text}`` dicts)."""
        with self._session_factory() as session:
            rows = session.query(SnippetRow).order_by(func.lower(SnippetRow.trigger)).all()
            return [self._snippet_dict(r) for r in rows]

    def get_snippets(self) -> list[Snippet]:
        """Snippets ready for ``mark_snippets``."""
        with self._session_factory() as session:
            return [self._snippet(r) for r in session.query(SnippetRow).all()]

    def add_snippet(self, snippet: Snippet) -> dict | None:
        """Insert a snippet, or replace the one with the same trigger (any case), like ``add_dictionary_entry``.

        Returns the snippet, or None when it has no trigger or nothing to insert.
        """
        cleaned = self._clean_snippet(snippet)
        if cleaned is None:
            return None
        trigger, options = cleaned
        with self._session_factory() as session:
            row = session.query(SnippetRow).filter(func.lower(SnippetRow.trigger) == trigger.lower()).first()
            if row is None:
                row = SnippetRow()
                session.add(row)
            row.trigger, row.expansion, row.options = trigger, snippet.expansion, options
            session.commit()
            return self._snippet_dict(row)

    def update_snippet(self, snippet_id: int, snippet: Snippet) -> dict | None:
        """Change the snippet with ``snippet_id``.

        Returns the snippet, or None when it does not exist, has no trigger or nothing
        to insert, or its new trigger already belongs to another snippet.
        """
        cleaned = self._clean_snippet(snippet)
        if cleaned is None:
            return None
        trigger, options = cleaned
        with self._session_factory() as session:
            row = session.get(SnippetRow, snippet_id)
            clash = session.query(SnippetRow).filter(func.lower(SnippetRow.trigger) == trigger.lower()).first()
            if row is None or (clash is not None and clash.id != row.id):
                return None
            row.trigger, row.expansion, row.options = trigger, snippet.expansion, options
            session.commit()
            return self._snippet_dict(row)

    def delete_snippet(self, snippet_id: int) -> bool:
        with self._session_factory() as session:
            row = session.get(SnippetRow, snippet_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # ---- Polish presets -----------------------------------------------------

    @staticmethod
    def _preset_dict(row: PolishPresetRow) -> dict:
        return {"id": row.id, "name": row.name, "instruction": row.instruction, "is_default": row.is_default}

    def list_polish_presets(self) -> list[dict]:
        """All presets, sorted by name, as plain dicts."""
        with self._session_factory() as session:
            rows = session.query(PolishPresetRow).all()
            # Sorted in Python: SQLite's lower() folds ASCII only, and a decomposed key puts "über" with the u's.
            rows.sort(key=lambda r: unicodedata.normalize("NFKD", r.name.casefold()))
            return [self._preset_dict(r) for r in rows]

    def get_polish_presets(self) -> list[PolishPreset]:
        """Presets ready for ``polish_presets.resolve_instruction``."""
        with self._session_factory() as session:
            return [PolishPreset(r.name, r.instruction, r.is_default) for r in session.query(PolishPresetRow).all()]

    def save_polish_preset(self, preset_id: int | None, preset: PolishPreset) -> dict | None:
        """Add a preset (``preset_id`` None) or change one. Making it the default clears the old default.

        Returns the preset, or None when the name or instruction is blank, the name has more words
        than a spoken name can match (`MAX_PRESET_NAME_WORDS`), the name belongs to another preset
        (any case), or ``preset_id`` does not exist.
        """
        name, instruction = " ".join(preset.name.split()), preset.instruction.strip()
        if not name or not instruction or len(name.split()) > MAX_PRESET_NAME_WORDS:
            return None
        with self._session_factory() as session:
            # Compared in Python: SQLite's lower() folds ASCII only, so "ÜBER" and "über" would both be saved.
            key = name.casefold()
            clash = next((r for r in session.query(PolishPresetRow).all() if r.name.casefold() == key), None)
            row = PolishPresetRow() if preset_id is None else session.get(PolishPresetRow, preset_id)
            if row is None or (clash is not None and clash.id != row.id):
                return None
            if preset.is_default:
                session.query(PolishPresetRow).update({PolishPresetRow.is_default: False})
            row.name, row.instruction, row.is_default = name, instruction, preset.is_default
            if preset_id is None:
                session.add(row)
            session.commit()
            return self._preset_dict(row)

    def delete_polish_preset(self, preset_id: int) -> bool:
        with self._session_factory() as session:
            row = session.get(PolishPresetRow, preset_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # ---- File transcriptions ------------------------------------------------

    def save_file_transcription(
        self,
        text: str,
        language: str,
        duration_ms: int,
        original_filename: str,
        model: str | None = None,
        confidence: float | None = None,
        words: list[Word] | None = None,
        cleaned_text: str | None = None,
        cleanup_level: str | None = None,
    ) -> FileTranscription:
        """Save a file-upload transcription record, with per-word timing when there is any.

        `cleaned_text` is the LLM-cleaned version of `text` (which stays as transcribed).
        """
        with self._session_factory() as session:
            record = FileTranscription(
                text=text,
                language=language,
                duration_ms=duration_ms,
                original_filename=original_filename,
                created_at=utc_now(),
                model=model,
                confidence=confidence,
                words=json.dumps([_word_json(w) for w in words]) if words else None,
                cleaned_text=cleaned_text,
                cleanup_level=cleanup_level if cleaned_text else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_file_transcriptions(self, limit: int = 50) -> list[FileTranscription]:
        """Get file transcriptions, newest first."""
        with self._session_factory() as session:
            return (
                session.query(FileTranscription)
                .order_by(FileTranscription.created_at.desc(), FileTranscription.id.desc())
                .limit(limit)
                .all()
            )
