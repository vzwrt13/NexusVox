"""Tests for dashboard analytics query functions."""

from __future__ import annotations

from datetime import datetime

from nexusvox.dashboard.analytics import (
    get_activity_heatmap,
    get_flagged_transcriptions,
    get_language_distribution,
    get_overview,
    get_peak_usage_hours,
    get_top_words,
    get_transcriptions_over_time,
    get_unreviewed_transcriptions,
)
from nexusvox.models import Transcription


def _seed(session_factory, records):
    """Insert transcription records directly for precise control over created_at."""
    with session_factory() as session:
        for r in records:
            session.add(Transcription(**r))
        session.commit()


# ---- get_overview ----------------------------------------------------------


def test_overview_empty_db(session_factory):
    result = get_overview(session_factory)

    assert result["total_transcriptions"] == 0
    assert result["avg_duration_ms"] == 0
    assert result["avg_wpm"] == 0
    assert result["time_saved_minutes"] == 0


def test_overview_with_data(session_factory, sample_transcriptions):
    result = get_overview(session_factory)

    assert result["total_transcriptions"] == 8
    assert result["avg_duration_ms"] > 0
    assert result["avg_wpm"] > 0


def test_overview_time_saved_not_negative(session_factory):
    """Even with very slow dictation, time_saved should be >= 0."""
    _seed(
        session_factory,
        [
            {"text": "hi", "language": "en", "duration_ms": 600_000, "created_at": datetime(2026, 3, 15)},
        ],
    )

    result = get_overview(session_factory)
    assert result["time_saved_minutes"] >= 0


def test_overview_avg_confidence(session_factory):
    """Average confidence computed only from records that have one."""
    _seed(
        session_factory,
        [
            {
                "text": "a b c",
                "language": "en",
                "duration_ms": 1000,
                "confidence": 0.90,
                "created_at": datetime(2026, 3, 15),
            },
            {
                "text": "d e f",
                "language": "en",
                "duration_ms": 1000,
                "confidence": 0.80,
                "created_at": datetime(2026, 3, 15),
            },
            {
                "text": "g h i",
                "language": "en",
                "duration_ms": 1000,
                "confidence": None,
                "created_at": datetime(2026, 3, 15),
            },
        ],
    )

    result = get_overview(session_factory)
    assert result["avg_confidence"] is not None
    assert abs(result["avg_confidence"] - 0.85) < 0.01


# ---- get_transcriptions_over_time -----------------------------------------


def test_transcriptions_over_time_day(session_factory):
    _seed(
        session_factory,
        [
            {"text": "day one a", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 10, 9, 0)},
            {"text": "day one b", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 10, 14, 0)},
            {"text": "day two", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 12, 10, 0)},
        ],
    )

    result = get_transcriptions_over_time(session_factory, period="day")

    assert "2026-03-10" in result["labels"]
    assert "2026-03-12" in result["labels"]
    idx_10 = result["labels"].index("2026-03-10")
    assert result["values"][idx_10] == 2


# ---- get_language_distribution ---------------------------------------------


def test_language_distribution(session_factory):
    _seed(
        session_factory,
        [
            {"text": "hello", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15)},
            {"text": "world", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15)},
            {"text": "hallo", "language": "de", "duration_ms": 1000, "created_at": datetime(2026, 3, 15)},
        ],
    )

    result = get_language_distribution(session_factory)

    assert "EN" in result["labels"]
    assert "DE" in result["labels"]
    total = sum(result["values"])
    assert total == 3


# ---- get_top_words ---------------------------------------------------------


def test_top_words(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "test test test hello hello world",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 15),
            },
        ],
    )

    result = get_top_words(session_factory, n=3)

    assert result["labels"][0] == "test"
    assert result["values"][0] == 3


def test_top_words_skips_single_char(session_factory):
    """Single-character words (after punctuation stripping) should be excluded."""
    _seed(
        session_factory,
        [
            {"text": "I a the test", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15)},
        ],
    )

    result = get_top_words(session_factory, n=10)

    # "I" and "a" are single chars after lowering — should be excluded
    assert "i" not in result["labels"]
    assert "a" not in result["labels"]
    assert "the" in result["labels"]


# ---- get_peak_usage_hours --------------------------------------------------


def test_peak_usage_hours_24_entries(session_factory):
    _seed(
        session_factory,
        [{"text": "test", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15, 14, 30)}],
    )

    result = get_peak_usage_hours(session_factory)

    assert len(result["labels"]) == 24
    assert len(result["values"]) == 24
    assert result["labels"][0] == "00"
    assert result["labels"][23] == "23"


# ---- get_activity_heatmap --------------------------------------------------


def test_activity_heatmap_7x24(session_factory):
    _seed(
        session_factory,
        [{"text": "test", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15, 10, 0)}],
    )

    result = get_activity_heatmap(session_factory)

    assert len(result["days"]) == 7
    assert len(result["hours"]) == 24
    assert len(result["grid"]) == 7
    assert all(len(row) == 24 for row in result["grid"])


# ---- get_flagged_transcriptions -------------------------------------------


def test_flagged_transcriptions_dict_shape(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "flagged text",
                "language": "en",
                "duration_ms": 1000,
                "flagged": 1,
                "corrected_text": "correct text",
                "confidence": 0.85,
                "created_at": datetime(2026, 3, 15),
            }
        ],
    )

    result = get_flagged_transcriptions(session_factory)

    assert len(result) == 1
    entry = result[0]
    assert set(entry.keys()) == {"id", "text", "corrected_text", "confidence", "created_at", "language", "audio_path"}
    assert entry["text"] == "flagged text"
    assert entry["corrected_text"] == "correct text"


# ---- date filtering -------------------------------------------------------


def test_date_filter_narrows_results(session_factory):
    _seed(
        session_factory,
        [
            {"text": "january", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 1, 15)},
            {"text": "march a", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 10)},
            {"text": "march b", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 20)},
        ],
    )

    result = get_overview(session_factory, start="2026-03-01", end="2026-03-31")

    assert result["total_transcriptions"] == 2


# ---- get_unreviewed_transcriptions ----------------------------------------


def test_unreviewed_transcriptions_dict_shape(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "unreviewed text",
                "language": "en",
                "duration_ms": 1000,
                "audio_path": "audio/1.wav",
                "flagged": 1,
                "created_at": datetime(2026, 3, 15),
            }
        ],
    )

    result = get_unreviewed_transcriptions(session_factory)

    assert len(result) == 1
    entry = result[0]
    assert set(entry.keys()) == {
        "id",
        "text",
        "corrected_text",
        "confidence",
        "created_at",
        "language",
        "audio_path",
        "flagged",
        "duration_ms",
    }
    assert entry["flagged"] == 1


def test_unreviewed_excludes_no_audio(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "has audio",
                "language": "en",
                "duration_ms": 1000,
                "audio_path": "audio/1.wav",
                "created_at": datetime(2026, 3, 15),
            },
            {"text": "no audio", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15)},
        ],
    )

    result = get_unreviewed_transcriptions(session_factory)
    assert len(result) == 1
    assert result[0]["text"] == "has audio"


def test_unreviewed_excludes_reviewed(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "reviewed",
                "language": "en",
                "duration_ms": 1000,
                "audio_path": "audio/1.wav",
                "reviewed": 1,
                "created_at": datetime(2026, 3, 15),
            },
            {
                "text": "unreviewed",
                "language": "en",
                "duration_ms": 1000,
                "audio_path": "audio/2.wav",
                "created_at": datetime(2026, 3, 15),
            },
        ],
    )

    result = get_unreviewed_transcriptions(session_factory)
    assert len(result) == 1
    assert result[0]["text"] == "unreviewed"
