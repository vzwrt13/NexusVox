"""Database session management."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import DatabaseConfig
from .models import Base, FileTranscription, Transcription

logger = logging.getLogger(__name__)


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
    ) -> Transcription:
        """Save a transcription record to the database."""
        with self._session_factory() as session:
            record = Transcription(
                text=text,
                language=language,
                duration_ms=duration_ms,
                created_at=datetime.utcnow(),
                confidence=confidence,
                model=model,
                audio_path=audio_path,
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
        """Get unreviewed transcriptions that have audio, oldest first."""
        with self._session_factory() as session:
            return (
                session.query(Transcription)
                .filter(Transcription.reviewed == 0, Transcription.audio_path.isnot(None))
                .order_by(Transcription.created_at.asc(), Transcription.id.asc())
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

    # ---- File transcriptions ------------------------------------------------

    def save_file_transcription(
        self,
        text: str,
        language: str,
        duration_ms: int,
        original_filename: str,
        model: str | None = None,
        confidence: float | None = None,
    ) -> FileTranscription:
        """Save a file-upload transcription record."""
        with self._session_factory() as session:
            record = FileTranscription(
                text=text,
                language=language,
                duration_ms=duration_ms,
                original_filename=original_filename,
                created_at=datetime.utcnow(),
                model=model,
                confidence=confidence,
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
