"""SQLAlchemy table definitions."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Float, Integer, String, Text
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
    # Translate-before-inject: what happened on the way to the cursor. The text above
    # is always the spoken one; these say whether English was typed instead.
    translated: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    translate_source: Mapped[str | None] = mapped_column(String(5), nullable=True)
    translate_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    translate_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Timing, both measured from the moment recording stopped: how long the
    # transcript took to arrive, and how long until the text was at the cursor.
    transcribe_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    # JSON list of {"word", "start", "end"} (seconds from the file's start), plus "speaker" (1, 2, ...)
    # when speaker detection ran; NULL when the engine reported no word timing.
    words: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The transcript after the optional LLM cleanup (`text` stays as transcribed) and the level used
    # ("light" / "medium"); both NULL when cleanup was not asked for or changed nothing.
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleanup_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Where the row comes from: "upload" (a file) or "meeting" (a two-track recording; speaker 1 is the
    # microphone, 2 and up the system audio).
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="upload", server_default="upload")
    # JSON object {"1": "Alex"}: names that replace "Speaker N" in `text`; NULL when every speaker is numbered.
    speaker_names: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Playable audio kept for the row (a meeting's mix), relative to the database folder; NULL for uploads.
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DictionaryEntryRow(Base):
    """A correction-dictionary entry: ``wrong`` (as transcribed) -> ``right`` (as intended)."""

    __tablename__ = "dictionary_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wrong: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    right: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class SnippetRow(Base):
    """A user-defined snippet: the spoken ``trigger`` expands into ``expansion`` (any length).

    ``options`` is a JSON list of ``[name, text]`` pairs; when set, the words after the
    trigger pick the best-matching option and ``expansion`` is only the fallback.
    """

    __tablename__ = "snippets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    expansion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    options: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class PolishPresetRow(Base):
    """A named polish instruction; saying ``name`` uses ``instruction``. At most one row is the default,
    used when the polish key is tapped without an instruction."""

    __tablename__ = "polish_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DictionarySuggestionRow(Base):
    """A pending dictionary entry learned from a correction after a paste, awaiting the user's OK."""

    __tablename__ = "dictionary_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wrong: Mapped[str] = mapped_column(String(500), nullable=False)
    right: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
