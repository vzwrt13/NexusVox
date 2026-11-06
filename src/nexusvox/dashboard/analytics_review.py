"""Dashboard analytics: the rows the Edit and Review tabs list."""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from ..models import Transcription


def get_flagged_transcriptions(session_factory: sessionmaker, limit: int = 50) -> list[dict]:
    """Return flagged transcriptions for the Edit tab."""
    with session_factory() as session:
        rows = (
            session.query(Transcription)
            .filter(Transcription.flagged == 1)
            .order_by(Transcription.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "text": r.text,
                "corrected_text": r.corrected_text,
                "confidence": round(r.confidence, 4) if r.confidence is not None else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "language": r.language,
                "audio_path": r.audio_path,
            }
            for r in rows
        ]


def get_unreviewed_transcriptions(
    session_factory: sessionmaker, limit: int = 50, *, before_id: int | None = None
) -> dict:
    """Return a page of unreviewed transcriptions that have audio, newest first.

    ``before_id`` is a cursor: only rows with a smaller id are returned, so the
    client can page through the backlog by passing the last id it has seen.
    ``remaining`` counts the unreviewed rows older than the returned page. The page is sorted by id, like the
    cursor: sorted by ``created_at``, a corrected system clock would make pages skip rows.
    """
    with session_factory() as session:
        base = session.query(Transcription).filter(Transcription.reviewed == 0, Transcription.audio_path.isnot(None))
        if before_id is not None:
            base = base.filter(Transcription.id < before_id)
        rows = base.order_by(Transcription.id.desc()).limit(limit).all()
        remaining = base.filter(Transcription.id < rows[-1].id).count() if rows else 0
        return {
            "items": [
                {
                    "id": r.id,
                    "text": r.text,
                    "corrected_text": r.corrected_text,
                    "confidence": round(r.confidence, 4) if r.confidence is not None else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "language": r.language,
                    "audio_path": r.audio_path,
                    "flagged": r.flagged,
                    "duration_ms": r.duration_ms,
                }
                for r in rows
            ],
            "remaining": remaining,
        }
