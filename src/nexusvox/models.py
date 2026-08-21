"""SQLAlchemy table definitions."""

from datetime import UTC, datetime

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime.

    Naive UTC is what the schema and every row already written assume, and that
    contract is kept deliberately rather than modernised. SQLite has no datetime
    type, so these are stored as ISO strings; making them timezone-aware would
    append a "+00:00" offset that `analytics.py` cannot handle -- it compares
    `created_at` against plain date strings and runs SQLite `strftime()` over the
    column. New aware rows would also sort inconsistently against existing naive
    ones, and the API output format would change.

    `datetime.utcnow()` produced exactly this value but is deprecated and
    scheduled for removal, which would break an unmaintained install silently.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Transcription(Base):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    flagged: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class FileTranscription(Base):
    __tablename__ = "file_transcriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
