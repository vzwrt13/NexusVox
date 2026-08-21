"""Tests for database CRUD operations."""

from __future__ import annotations


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


def test_get_unreviewed_oldest_first(db):
    db.save_transcription("first", "en", 1000, audio_path="audio/1.wav")
    db.save_transcription("second", "en", 1000, audio_path="audio/2.wav")

    result = db.get_unreviewed()
    assert result[0].text == "first"
    assert result[1].text == "second"


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
