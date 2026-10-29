"""Tests for database CRUD operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexusvox.dictionary import DictionaryEntry
from nexusvox.models import utc_now
from nexusvox.snippets import Snippet, SnippetOption


def test_utc_now_is_naive_utc():
    """Timestamps must stay naive, and must actually be UTC.

    analytics.py compares created_at against plain date strings and runs SQLite
    strftime() over the column, neither of which survives a "+00:00" offset. It
    would also sort new rows inconsistently against every row already stored. If
    this test ever fails because someone made the timestamps timezone-aware,
    that change needs a schema migration and a rewrite of the analytics queries,
    not a fix to this assertion.
    """
    now = utc_now()

    assert now.tzinfo is None, "timestamps must be naive; see utc_now() docstring"
    # Naive, but genuinely UTC rather than local time.
    assert abs(now - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=5)


def test_saved_timestamps_are_naive(db):
    record = db.save_transcription("hello", "en", 1000)

    assert record.created_at is not None
    assert record.created_at.tzinfo is None


def test_save_transcription_returns_record(db):
    record = db.save_transcription("hello world", "en", 1200)

    assert record.id is not None
    assert record.text == "hello world"
    assert record.language == "en"
    assert record.duration_ms == 1200
    assert record.created_at is not None
    assert record.confidence is None
    assert record.flagged == 0


def test_save_transcription_with_confidence(db):
    record = db.save_transcription("hi", "en", 500, confidence=0.95)
    assert record.confidence == 0.95


def test_save_transcription_timing_defaults_to_none(db):
    record = db.save_transcription("hi", "en", 500)
    assert record.transcribe_ms is None
    assert record.latency_ms is None


def test_save_transcription_with_timing(db):
    record = db.save_transcription("hi", "en", 500, transcribe_ms=320, latency_ms=610)
    assert record.transcribe_ms == 320
    assert record.latency_ms == 610


def test_save_transcription_with_audio_path(db):
    record = db.save_transcription("hi", "en", 500, audio_path="audio/1_123.wav")
    assert record.audio_path == "audio/1_123.wav"


def test_save_transcription_audio_path_default_none(db):
    record = db.save_transcription("hi", "en", 500)
    assert record.audio_path is None


def test_update_audio_path(db):
    record = db.save_transcription("hi", "en", 500)
    db.update_audio_path(record.id, "audio/1_456.wav")
    flagged = db.get_recent(limit=1)
    assert flagged[0].audio_path == "audio/1_456.wav"


def test_get_recent_newest_first(db):
    db.save_transcription("first", "en", 1000)
    db.save_transcription("second", "en", 1000)
    db.save_transcription("third", "en", 1000)

    recent = db.get_recent(limit=3)

    assert len(recent) == 3
    assert recent[0].text == "third"
    assert recent[2].text == "first"


def test_get_recent_respects_limit(db):
    for i in range(5):
        db.save_transcription(f"text {i}", "en", 1000)

    recent = db.get_recent(limit=2)
    assert len(recent) == 2


def test_get_recent_empty_db(db):
    assert db.get_recent() == []


def test_flag_transcription_success(db):
    record = db.save_transcription("test", "en", 1000)

    result = db.flag_transcription(record.id)

    assert result is True
    flagged = db.get_flagged()
    assert len(flagged) == 1
    assert flagged[0].id == record.id
    assert flagged[0].flagged == 1


def test_flag_nonexistent_id(db):
    assert db.flag_transcription(9999) is False


def test_update_correction_success(db):
    record = db.save_transcription("tset", "en", 1000)
    db.flag_transcription(record.id)

    result = db.update_correction(record.id, "test")

    assert result is True
    flagged = db.get_flagged()
    assert flagged[0].corrected_text == "test"


def test_update_correction_nonexistent_id(db):
    assert db.update_correction(9999, "text") is False


def test_get_flagged_only_flagged(db):
    db.save_transcription("normal", "en", 1000)
    r2 = db.save_transcription("flagged one", "en", 1000)
    db.save_transcription("also normal", "en", 1000)

    db.flag_transcription(r2.id)

    flagged = db.get_flagged()
    assert len(flagged) == 1
    assert flagged[0].text == "flagged one"


def test_get_flagged_empty_when_none_flagged(db):
    db.save_transcription("test", "en", 1000)
    assert db.get_flagged() == []


def test_get_last_transcription_id(db):
    db.save_transcription("first", "en", 1000)
    db.save_transcription("second", "en", 1000)
    last = db.save_transcription("third", "en", 1000)

    assert db.get_last_transcription_id() == last.id


def test_get_last_transcription_id_empty_db(db):
    assert db.get_last_transcription_id() is None


# ── File transcription CRUD ──────────────────────────────────────────


def test_save_file_transcription(db):
    record = db.save_file_transcription(
        text="hello from file",
        language="en",
        duration_ms=5000,
        original_filename="test.wav",
        model="parakeet-tdt-0.6b",
    )

    assert record.id is not None
    assert record.text == "hello from file"
    assert record.language == "en"
    assert record.duration_ms == 5000
    assert record.original_filename == "test.wav"
    assert record.model == "parakeet-tdt-0.6b"
    assert record.confidence is None
    assert record.created_at is not None


def test_save_file_transcription_with_confidence(db):
    record = db.save_file_transcription(
        text="hi",
        language="en",
        duration_ms=1000,
        original_filename="audio.mp3",
        confidence=0.91,
    )
    assert record.confidence == 0.91


def test_save_file_transcription_keeps_cleaned_text_beside_the_original(db):
    record = db.save_file_transcription(
        "well um hello", "en", 1000, "a.wav", cleaned_text="Well, hello.", cleanup_level="light"
    )

    stored = db.get_file_transcriptions(limit=1)[0]
    assert (stored.id, stored.text) == (record.id, "well um hello")
    assert (stored.cleaned_text, stored.cleanup_level) == ("Well, hello.", "light")


def test_file_transcription_without_cleanup_has_no_cleaned_text(db):
    record = db.save_file_transcription("hello", "en", 1000, "a.wav", cleanup_level="light")

    assert record.cleaned_text is None and record.cleanup_level is None


def test_migration_adds_the_cleanup_columns_to_an_old_database(tmp_path):
    import sqlite3

    from nexusvox.config import DatabaseConfig
    from nexusvox.db import Database

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE file_transcriptions (id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
            " language VARCHAR(5) NOT NULL, duration_ms INTEGER NOT NULL, created_at DATETIME, model VARCHAR(100),"
            " original_filename VARCHAR(255) NOT NULL, confidence FLOAT, words TEXT)"
        )
        conn.execute(
            "INSERT INTO file_transcriptions (text, language, duration_ms, original_filename)"
            " VALUES ('hi', 'en', 1, 'a.wav')"
        )

    old = Database(DatabaseConfig(path=str(path)))

    row = old.get_file_transcriptions(limit=1)[0]
    assert row.text == "hi" and row.cleaned_text is None and row.cleanup_level is None


def test_migration_adds_the_title_column_to_an_old_database(tmp_path):
    import sqlite3

    from nexusvox.config import DatabaseConfig
    from nexusvox.db import Database

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE file_transcriptions (id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
            " language VARCHAR(5) NOT NULL, duration_ms INTEGER NOT NULL, created_at DATETIME, model VARCHAR(100),"
            " original_filename VARCHAR(255) NOT NULL, confidence FLOAT, words TEXT)"
        )
        conn.execute(
            "INSERT INTO file_transcriptions (text, language, duration_ms, original_filename)"
            " VALUES ('hi', 'en', 1, 'a.wav')"
        )

    old = Database(DatabaseConfig(path=str(path)))

    row = old.get_file_transcriptions(limit=1)[0]
    assert row.title is None and row.original_filename == "a.wav"
    assert old.set_file_transcription_title(row.id, "Renamed").title == "Renamed"
    assert old.set_file_transcription_title(999, "x") is None


def test_get_file_transcriptions_newest_first(db):
    db.save_file_transcription("first", "en", 1000, "a.wav")
    db.save_file_transcription("second", "en", 1000, "b.wav")
    db.save_file_transcription("third", "en", 1000, "c.wav")

    results = db.get_file_transcriptions(limit=3)

    assert len(results) == 3
    assert results[0].text == "third"
    assert results[2].text == "first"


def test_ordering_is_stable_when_timestamps_collide(db, session_factory):
    """Records sharing a created_at must still come back in insertion order.

    On Windows before Python 3.13 the system clock has ~15.6 ms granularity, so
    consecutive writes land on identical timestamps and ordering by created_at
    alone is non-deterministic. The queries break the tie on the autoincrement
    id; this forces the collision so the guarantee holds on every platform.
    """
    from nexusvox.models import FileTranscription, Transcription

    db.save_transcription("first", "en", 1000)
    db.save_transcription("second", "en", 1000)
    db.save_transcription("third", "en", 1000)
    db.save_file_transcription("file first", "en", 1000, "a.wav")
    db.save_file_transcription("file second", "en", 1000, "b.wav")

    with session_factory() as session:
        shared = session.query(Transcription).order_by(Transcription.id.asc()).first().created_at
        for row in session.query(Transcription).all():
            row.created_at = shared
        for row in session.query(FileTranscription).all():
            row.created_at = shared
        session.commit()

    assert [r.text for r in db.get_recent(limit=3)] == ["third", "second", "first"]
    assert [r.text for r in db.get_file_transcriptions(limit=2)] == ["file second", "file first"]
    assert db.get_last_transcription_id() == 3


def test_get_file_transcriptions_respects_limit(db):
    for i in range(5):
        db.save_file_transcription(f"text {i}", "en", 1000, f"file{i}.wav")

    results = db.get_file_transcriptions(limit=2)
    assert len(results) == 2


def test_get_file_transcriptions_empty(db):
    assert db.get_file_transcriptions() == []


# ── Review CRUD ──────────────────────────────────────────────────────


def test_get_unreviewed_only_with_audio(db):
    db.save_transcription("has audio", "en", 1000, audio_path="audio/1.wav")
    db.save_transcription("no audio", "en", 1000)

    result = db.get_unreviewed()
    assert len(result) == 1
    assert result[0].text == "has audio"


def test_get_unreviewed_excludes_reviewed(db):
    r = db.save_transcription("reviewed", "en", 1000, audio_path="audio/1.wav")
    db.submit_review(r.id, is_correct=True)
    db.save_transcription("unreviewed", "en", 1000, audio_path="audio/2.wav")

    result = db.get_unreviewed()
    assert len(result) == 1
    assert result[0].text == "unreviewed"


def test_get_unreviewed_newest_first(db):
    db.save_transcription("first", "en", 1000, audio_path="audio/1.wav")
    db.save_transcription("second", "en", 1000, audio_path="audio/2.wav")

    result = db.get_unreviewed()
    assert result[0].text == "second"
    assert result[1].text == "first"


def test_submit_review_correct(db):
    r = db.save_transcription("test", "en", 1000, audio_path="audio/1.wav")
    ok = db.submit_review(r.id, is_correct=True)

    assert ok is True
    result = db.get_unreviewed()
    assert len(result) == 0


def test_submit_review_incorrect_with_correction(db):
    r = db.save_transcription("tset", "en", 1000, audio_path="audio/1.wav")
    ok = db.submit_review(r.id, is_correct=False, corrected_text="test")

    assert ok is True
    recent = db.get_recent(limit=1)
    assert recent[0].reviewed == 2
    assert recent[0].corrected_text == "test"


def test_submit_review_nonexistent_id(db):
    assert db.submit_review(9999, is_correct=True) is False


def test_save_transcription_translate_fields(db):
    record = db.save_transcription(
        text="Also ich glaube, das passt.",
        language="de",
        duration_ms=1200,
        translated=True,
        translate_source="de",
        translate_ms=450,
    )

    assert (record.translated, record.translate_source, record.translate_ms, record.translate_error) == (
        1,
        "de",
        450,
        None,
    )


def test_save_transcription_translate_defaults(db):
    record = db.save_transcription(text="Hello", language="en", duration_ms=800)

    assert (record.translated, record.translate_source, record.translate_ms, record.translate_error) == (
        0,
        None,
        None,
        None,
    )


# --- Dictionary suggestions (learn from edits) ---


def test_add_dictionary_suggestions_skips_duplicates_and_known_entries(db):
    db.add_dictionary_entry("nexus fox", "NexusVox")
    added = db.add_dictionary_suggestions(
        [
            DictionaryEntry(wrong="nexus fox", right="NexusVox"),
            DictionaryEntry(wrong="Maya", right="Meier"),
            DictionaryEntry(wrong="maya", right="Meier"),
        ]
    )

    assert added == 1
    assert [(s["wrong"], s["right"]) for s in db.list_dictionary_suggestions()] == [("Maya", "Meier")]


def test_accept_dictionary_suggestion_moves_it_into_the_dictionary(db):
    db.add_dictionary_suggestions([DictionaryEntry(wrong="Maya", right="Meier")])
    sid = db.list_dictionary_suggestions()[0]["id"]

    entry = db.accept_dictionary_suggestion(sid)

    assert (entry["wrong"], entry["right"]) == ("Maya", "Meier")
    assert db.list_dictionary_suggestions() == []
    assert db.accept_dictionary_suggestion(sid) is None


def test_delete_dictionary_suggestion(db):
    db.add_dictionary_suggestions([DictionaryEntry(wrong="Maya", right="Meier")])
    sid = db.list_dictionary_suggestions()[0]["id"]

    assert db.delete_dictionary_suggestion(sid) is True
    assert db.delete_dictionary_suggestion(sid) is False
    assert db.get_dictionary_entries() == []


def test_accept_dictionary_suggestion_drops_other_suggestions_for_the_same_phrase(db):
    db.add_dictionary_suggestions(
        [DictionaryEntry(wrong="Maya", right="Meier"), DictionaryEntry(wrong="maya", right="Mayer")]
    )
    sid = next(s["id"] for s in db.list_dictionary_suggestions() if s["right"] == "Meier")

    assert db.accept_dictionary_suggestion(sid)["right"] == "Meier"
    assert db.list_dictionary_suggestions() == []


# --- Snippets ---


def test_add_and_list_snippets(db):
    db.add_snippet(Snippet("my address", "Main Street 1\nSpringfield"))
    db.add_snippet(Snippet("  meine   Signatur ", "Viele Grüße"))

    assert [(s["trigger"], s["expansion"]) for s in db.list_snippets()] == [
        ("meine Signatur", "Viele Grüße"),
        ("my address", "Main Street 1\nSpringfield"),
    ]
    assert {s.trigger for s in db.get_snippets()} == {"my address", "meine Signatur"}


def test_add_snippet_same_trigger_replaces(db):
    db.add_snippet(Snippet("my address", "old"))
    db.add_snippet(Snippet("My Address", "new"))

    assert [(s["trigger"], s["expansion"]) for s in db.list_snippets()] == [("My Address", "new")]


def test_update_snippet_and_trigger_clash(db):
    first = db.add_snippet(Snippet("one", "1"))
    db.add_snippet(Snippet("two", "2"))

    assert db.update_snippet(first["id"], Snippet("uno", "1!"))["trigger"] == "uno"
    assert db.update_snippet(first["id"], Snippet("two", "clash")) is None
    assert db.update_snippet(999, Snippet("x", "y")) is None


def test_snippet_needs_a_trigger_and_something_to_insert(db):
    assert db.add_snippet(Snippet(" ", "text")) is None
    assert db.add_snippet(Snippet("trigger", "  ")) is None
    assert db.add_snippet(Snippet("open remote repo", "", (SnippetOption("a", "b"),))) is not None


def test_delete_snippet(db):
    sid = db.add_snippet(Snippet("one", "1"))["id"]

    assert db.delete_snippet(sid) is True
    assert db.delete_snippet(sid) is False
    assert db.list_snippets() == []


def test_snippet_options_roundtrip(db):
    options = (SnippetOption("NexusVox", "https://a"), SnippetOption("sprechblase", "https://b"))
    db.add_snippet(Snippet("open remote repo", "", options))

    assert db.list_snippets()[0]["options"] == [
        {"name": "NexusVox", "text": "https://a"},
        {"name": "sprechblase", "text": "https://b"},
    ]
    assert db.get_snippets()[0].options == options


def test_file_transcription_stores_words_as_json(db):
    import json

    from nexusvox.transcriber import Word

    record = db.save_file_transcription(
        "hi there", "en", 1000, "a.wav", words=[Word("hi", 0.0, 0.2), Word("there", 0.3, 0.6)]
    )

    assert json.loads(record.words) == [
        {"word": "hi", "start": 0.0, "end": 0.2},
        {"word": "there", "start": 0.3, "end": 0.6},
    ]


def test_file_transcription_stores_speakers_of_labelled_words(db):
    import json

    from nexusvox.transcriber import Word

    words = [Word("hi", 0.0, 0.2, speaker=1), Word("there", 0.3, 0.6, speaker=2)]
    record = db.save_file_transcription("Speaker 1: hi\n\nSpeaker 2: there", "en", 1000, "a.wav", words=words)

    assert json.loads(record.words) == [
        {"word": "hi", "start": 0.0, "end": 0.2, "speaker": 1},
        {"word": "there", "start": 0.3, "end": 0.6, "speaker": 2},
    ]


def test_file_transcription_without_words_stores_null(db):
    assert db.save_file_transcription("hi", "en", 1000, "a.wav").words is None


def test_migration_adds_words_column_to_old_file_transcriptions(tmp_path):
    import sqlite3

    from nexusvox.config import DatabaseConfig
    from nexusvox.db import Database

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE file_transcriptions (id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
            " language VARCHAR(5) NOT NULL,"
            " duration_ms INTEGER NOT NULL, created_at DATETIME, model VARCHAR(100),"
            " original_filename VARCHAR(255) NOT NULL, confidence FLOAT)"
        )
    conn.close()

    database = Database(DatabaseConfig(path=str(path)))

    assert database.save_file_transcription("hi", "en", 1000, "a.wav").words is None


def test_migration_adds_the_stats_columns_to_an_old_database(tmp_path):
    import sqlite3

    from nexusvox.config import DatabaseConfig
    from nexusvox.db import Database

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE transcriptions (id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
            " language VARCHAR(5) NOT NULL, duration_ms INTEGER NOT NULL, created_at DATETIME, confidence FLOAT)"
        )
        conn.execute(
            "CREATE TABLE file_transcriptions (id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
            " language VARCHAR(5) NOT NULL, duration_ms INTEGER NOT NULL, created_at DATETIME, model VARCHAR(100),"
            " original_filename VARCHAR(255) NOT NULL, confidence FLOAT)"
        )
        conn.execute("INSERT INTO transcriptions (text, language, duration_ms) VALUES ('hi', 'en', 1)")
        conn.execute(
            "INSERT INTO file_transcriptions (text, language, duration_ms, original_filename)"
            " VALUES ('hi', 'en', 1, 'a.wav')"
        )

    old = Database(DatabaseConfig(path=str(path)))

    assert old.get_file_transcriptions(limit=1)[0].processing_ms is None
    dictation = old.get_recent(1)[0]
    assert dictation.cleanup_status is None and dictation.cleanup_ms is None
    old.save_transcription("new", "en", 1, cleanup_status="applied", cleanup_ms=120)
    assert old.get_recent(1)[0].cleanup_status == "applied"
    assert dictation.app is None
    old.save_transcription("typed", "en", 1, app="outlook.exe")
    assert old.get_recent(1)[0].app == "outlook.exe"
    assert dictation.polish_status is None and dictation.polish_ms is None
    old.save_transcription("[polish] x", "en", 1, polish_status="timeout", polish_ms=3000)
    assert (old.get_recent(1)[0].polish_status, old.get_recent(1)[0].polish_ms) == ("timeout", 3000)


def test_migration_is_idempotent_on_a_database_that_already_has_the_columns(tmp_path):
    """Starting twice, or on a database another branch already migrated, must not fail or lose rows."""
    from nexusvox.config import DatabaseConfig
    from nexusvox.db import Database

    config = DatabaseConfig(path=str(tmp_path / "again.db"))
    first = Database(config)
    first.save_transcription("kept", "en", 1, cleanup_status="applied", cleanup_ms=5)
    first.save_file_transcription("kept", "en", 1, "a.wav", processing_ms=7)

    second = Database(config)

    assert second.get_recent(1)[0].cleanup_ms == 5
    assert second.get_file_transcriptions(limit=1)[0].processing_ms == 7
