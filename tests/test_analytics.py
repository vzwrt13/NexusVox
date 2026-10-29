"""Tests for dashboard analytics query functions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from nexusvox.dashboard.analytics import (
    get_accuracy_over_time,
    get_activity_heatmap,
    get_app_breakdown,
    get_dictionary_usage,
    get_flagged_transcriptions,
    get_language_distribution,
    get_latency_over_time,
    get_learn_outcomes,
    get_model_breakdown,
    get_most_corrected,
    get_overview,
    get_peak_usage_hours,
    get_top_words,
    get_transcriptions_over_time,
    get_unreviewed_transcriptions,
    get_upload_overview,
)
from nexusvox.dictionary import DictionaryEntry
from nexusvox.models import COMMAND_APP, DictionaryUsageRow, FileTranscription, Transcription, utc_now


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


def test_overview_translate_stats(session_factory):
    now = datetime.now()
    _seed(
        session_factory,
        [
            {"text": "a", "language": "de", "duration_ms": 1000, "created_at": now},
            {
                "text": "b",
                "language": "de",
                "duration_ms": 1000,
                "created_at": now,
                "translated": 1,
                "translate_source": "de",
                "translate_ms": 400,
            },
            {
                "text": "c",
                "language": "de",
                "duration_ms": 1000,
                "created_at": now,
                "translated": 1,
                "translate_source": "mixed",
                "translate_ms": 600,
            },
            {
                "text": "d",
                "language": "de",
                "duration_ms": 1000,
                "created_at": now,
                "translate_error": "URLError: nothing listening",
            },
        ],
    )

    result = get_overview(session_factory)

    assert result["translated_count"] == 2
    assert result["avg_translate_ms"] == 500
    assert result["translate_failures"] == 1


def test_overview_translate_stats_empty(session_factory):
    result = get_overview(session_factory)

    assert result["translated_count"] == 0
    assert result["avg_translate_ms"] is None
    assert result["translate_failures"] == 0


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


def test_overview_word_and_longest_stats(session_factory):
    _seed(
        session_factory,
        [
            {"text": "one two three", "language": "en", "duration_ms": 3000, "created_at": datetime(2026, 3, 15)},
            {"text": "four five", "language": "en", "duration_ms": 9000, "created_at": datetime(2026, 3, 15)},
            {"text": "a b c d e f", "language": "en", "duration_ms": 4000, "created_at": datetime(2026, 3, 15)},
        ],
    )

    result = get_overview(session_factory)

    assert result["total_words"] == 11
    assert result["longest_duration_ms"] == 9000
    assert result["longest_words"] == 6


def test_overview_timing_stats(session_factory):
    """Latency averages skip rows recorded before the timing columns existed."""
    _seed(
        session_factory,
        [
            {
                "text": "a",
                "language": "en",
                "duration_ms": 2000,
                "created_at": datetime(2026, 3, 15),
                "transcribe_ms": 400,
                "latency_ms": 700,
            },
            {
                "text": "b",
                "language": "en",
                "duration_ms": 4000,
                "created_at": datetime(2026, 3, 15),
                "transcribe_ms": 800,
                "latency_ms": 900,
            },
            {"text": "c", "language": "en", "duration_ms": 100_000, "created_at": datetime(2026, 3, 15)},
        ],
    )

    result = get_overview(session_factory)

    assert result["avg_transcribe_ms"] == 600
    assert result["avg_latency_ms"] == 800
    # (400 + 800) / (2000 + 4000); the untimed 100 s row must not dilute it.
    assert result["avg_rtf"] == 0.2


def test_overview_timing_stats_empty(session_factory):
    result = get_overview(session_factory)

    assert result["avg_transcribe_ms"] is None
    assert result["avg_latency_ms"] is None
    assert result["avg_rtf"] is None


def test_overview_timing_stats_untimed_rows_only(session_factory):
    _seed(
        session_factory,
        [{"text": "a", "language": "en", "duration_ms": 2000, "created_at": datetime(2026, 3, 15)}],
    )

    result = get_overview(session_factory)

    assert result["avg_transcribe_ms"] is None
    assert result["avg_rtf"] is None


# ---- get_latency_over_time ------------------------------------------------


def test_latency_over_time_by_day(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "a",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 15),
                "transcribe_ms": 100,
                "latency_ms": 300,
            },
            {
                "text": "b",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 15),
                "transcribe_ms": 300,
                "latency_ms": None,
            },
            {
                "text": "c",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 16),
                "transcribe_ms": 500,
                "latency_ms": 600,
            },
            {"text": "d", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 17)},
        ],
    )

    result = get_latency_over_time(session_factory, "day")

    assert result["labels"] == ["2026-03-15", "2026-03-16"]
    assert result["transcribe_ms"] == [200, 500]
    assert result["latency_ms"] == [300, 600]


def test_latency_over_time_empty(session_factory):
    result = get_latency_over_time(session_factory, "day")

    assert result == {"labels": [], "transcribe_ms": [], "latency_ms": []}


# ---- get_model_breakdown --------------------------------------------------


def test_model_breakdown(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "one two",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 15),
                "model": "parakeet-tdt-0.6b",
                "transcribe_ms": 200,
            },
            {
                "text": "three four five six",
                "language": "en",
                "duration_ms": 3000,
                "created_at": datetime(2026, 3, 15),
                "model": "parakeet-tdt-0.6b",
                "transcribe_ms": 400,
            },
            {
                "text": "seven",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 15),
                "model": "whisper-small",
            },
        ],
    )

    result = get_model_breakdown(session_factory)

    assert [m["model"] for m in result] == ["parakeet-tdt-0.6b", "whisper-small"]
    parakeet, whisper = result
    assert parakeet["count"] == 2
    assert parakeet["words"] == 6
    assert parakeet["avg_wpm"] == 90.0
    assert parakeet["avg_duration_ms"] == 2000
    assert parakeet["avg_transcribe_ms"] == 300
    assert whisper["count"] == 1
    assert whisper["avg_transcribe_ms"] is None


def test_model_breakdown_unknown_model(session_factory):
    _seed(
        session_factory,
        [{"text": "a", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 15)}],
    )

    result = get_model_breakdown(session_factory)

    assert result[0]["model"] == "unknown"


def test_model_breakdown_empty(session_factory):
    assert get_model_breakdown(session_factory) == []


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

    result = get_unreviewed_transcriptions(session_factory)["items"]

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

    result = get_unreviewed_transcriptions(session_factory)["items"]
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

    result = get_unreviewed_transcriptions(session_factory)["items"]
    assert len(result) == 1
    assert result[0]["text"] == "unreviewed"


def _seed_unreviewed(session_factory, count: int) -> None:
    _seed(
        session_factory,
        [
            {
                "text": f"rec {i}",
                "language": "en",
                "duration_ms": 1000,
                "audio_path": f"audio/{i}.wav",
                "created_at": datetime(2026, 3, 1) + timedelta(minutes=i),
            }
            for i in range(count)
        ],
    )


def test_unreviewed_newest_first(session_factory):
    _seed_unreviewed(session_factory, 3)

    result = get_unreviewed_transcriptions(session_factory)
    assert [r["text"] for r in result["items"]] == ["rec 2", "rec 1", "rec 0"]
    assert result["remaining"] == 0


def test_unreviewed_pages_with_before_id(session_factory):
    _seed_unreviewed(session_factory, 5)

    page1 = get_unreviewed_transcriptions(session_factory, limit=2)
    assert [r["text"] for r in page1["items"]] == ["rec 4", "rec 3"]
    assert page1["remaining"] == 3

    page2 = get_unreviewed_transcriptions(session_factory, limit=2, before_id=page1["items"][-1]["id"])
    assert [r["text"] for r in page2["items"]] == ["rec 2", "rec 1"]
    assert page2["remaining"] == 1

    page3 = get_unreviewed_transcriptions(session_factory, limit=2, before_id=page2["items"][-1]["id"])
    assert [r["text"] for r in page3["items"]] == ["rec 0"]
    assert page3["remaining"] == 0


def test_unreviewed_empty_page(session_factory):
    result = get_unreviewed_transcriptions(session_factory)
    assert result == {"items": [], "remaining": 0}


def test_overview_cleanup_stats(session_factory):
    now = datetime.now()
    rows = [
        ("applied", 200),
        ("applied", 400),
        ("unchanged", 300),
        ("rejected", 500),
        ("timeout", 3000),
        ("failed", None),
    ]
    _seed(
        session_factory,
        [{"text": "a", "language": "en", "duration_ms": 1000, "created_at": now}]  # cleanup never asked
        + [
            {
                "text": "a",
                "language": "en",
                "duration_ms": 1000,
                "created_at": now,
                "cleanup_status": s,
                "cleanup_ms": ms,
            }
            for s, ms in rows
        ],
    )

    result = get_overview(session_factory)

    assert result["cleanup_applied"] == 2
    assert result["cleanup_unchanged"] == 1
    assert result["cleanup_rejected"] == 1
    assert result["cleanup_timeouts"] == 1
    assert result["cleanup_failed"] == 1
    assert result["avg_cleanup_ms"] == 880  # (200 + 400 + 300 + 500 + 3000) / 5


def test_overview_cleanup_stats_empty(session_factory):
    result = get_overview(session_factory)

    assert result["cleanup_applied"] == 0
    assert result["avg_cleanup_ms"] is None


def test_overview_polish_stats(session_factory):
    now = datetime.now()

    def row(text, **fields):
        return {"text": text, "language": "en", "duration_ms": 1, "created_at": now, "app": COMMAND_APP, **fields}

    _seed(
        session_factory,
        [
            {"text": "[polish] looks like one", "language": "en", "duration_ms": 1000, "created_at": now},  # dictation
            row("[polish] formal", polish_status="applied", polish_ms=300),
            row("[polish] shorter", polish_status="applied", polish_ms=500),
            row("[polish] default", polish_status="timeout", polish_ms=3002),
            row("[polish] default", polish_status="failed", polish_ms=10),
            row("[extension] sort", polish_status="extension"),
            row("[polish] formal"),  # saved before the status existed: a polish that went through
            row("[extension] sort"),  # likewise
        ],
    )

    result = get_overview(session_factory)

    assert result["polish_applied"] == 3
    assert result["polish_extension"] == 2
    assert result["polish_timeouts"] == 1
    assert result["polish_failed"] == 1
    assert result["avg_polish_ms"] == 400  # (300 + 500) / 2: a timeout or failure is no model speed
    # polish attempts are no dictations: only the first row counts in the dictation totals
    assert result["total_transcriptions"] == 3  # the dictation and the two rows from before the status existed


def test_overview_polish_stats_empty(session_factory):
    assert get_overview(session_factory)["polish_applied"] == 0
    _seed(session_factory, [{"text": "a", "language": "en", "duration_ms": 1, "created_at": datetime.now()}])
    result = get_overview(session_factory)
    assert result["polish_applied"] == 0 and result["avg_polish_ms"] is None


def _upload(**fields):
    return FileTranscription(
        text="t", language="en", duration_ms=fields.pop("duration_ms", 3_600_000), original_filename="a.wav", **fields
    )


def test_upload_overview(session_factory):
    two = json.dumps(
        [{"word": "a", "start": 0, "end": 1, "speaker": 1}, {"word": "b", "start": 1, "end": 2, "speaker": 2}]
    )
    three = json.dumps([{"word": "a", "speaker": n} for n in (1, 2, 3)])
    with session_factory() as session:
        session.add_all(
            [
                _upload(processing_ms=72_000, words=two),
                _upload(processing_ms=144_000, words=three),
                _upload(),  # from before processing time was measured: counts, but not in the timing
                _upload(words=json.dumps([{"word": "a", "start": 0, "end": 1}])),  # no speaker detection
                _upload(kind="meeting", processing_ms=1, words=two),  # meetings are not uploads
            ]
        )
        session.commit()

    result = get_upload_overview(session_factory)

    assert result["upload_count"] == 4
    assert result["upload_audio_hours"] == 4.0
    assert result["avg_upload_processing_ms"] == 108_000
    assert result["upload_rtf"] == 0.03  # 216 s of processing for 2 h of audio
    assert result["avg_upload_speakers"] == 2.5


def test_upload_overview_empty_and_date_range(session_factory):
    empty = get_upload_overview(session_factory)
    assert empty["upload_count"] == 0 and empty["avg_upload_processing_ms"] is None
    assert empty["upload_rtf"] is None and empty["avg_upload_speakers"] is None

    with session_factory() as session:
        session.add_all([_upload(created_at=datetime(2026, 1, 5)), _upload(created_at=datetime(2026, 3, 5))])
        session.commit()

    assert get_upload_overview(session_factory, start="2026-03-01", end="2026-03-31")["upload_count"] == 1


def test_upload_overview_ignores_broken_words_json(session_factory):
    with session_factory() as session:
        session.add(_upload(words="not json"))
        session.commit()

    assert get_upload_overview(session_factory)["avg_upload_speakers"] is None


# ---- get_app_breakdown ----------------------------------------------------


def _dictation(app, text="one two", day=15):
    return {"text": text, "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, day), "app": app}


def test_app_breakdown_groups_by_friendly_name(session_factory):
    _seed(
        session_factory,
        [
            _dictation("outlook.exe"),
            _dictation("olk.exe", "a b c"),
            _dictation("code.exe"),
            _dictation("orca.exe"),
            _dictation("orca.exe"),
            _dictation("another.exe"),
            _dictation(None),
        ],
    )

    result = get_app_breakdown(session_factory)

    assert result == [
        {"app": "Outlook", "count": 2, "words": 5},
        {"app": "orca", "count": 2, "words": 4},
        {"app": "VS Code", "count": 1, "words": 2},
        {"app": "another", "count": 1, "words": 2},
        {"app": "Unknown", "count": 1, "words": 2},
    ]


def test_app_breakdown_folds_the_tail_into_other(session_factory):
    rows = [_dictation(f"app{i:02d}.exe") for i in range(12)] + [_dictation("app00.exe")]
    _seed(session_factory, rows + [_dictation(None)])

    result = get_app_breakdown(session_factory)

    assert [g["app"] for g in result][:2] == ["app00", "app01"]
    assert result[-2:] == [{"app": "Other", "count": 2, "words": 4}, {"app": "Unknown", "count": 1, "words": 2}]
    assert len(result) == 12


def test_app_breakdown_puts_other_and_unknown_last_even_when_biggest(session_factory):
    _seed(session_factory, [_dictation(None), _dictation(None), _dictation("x.exe"), _dictation("code.exe")])

    assert [g["app"] for g in get_app_breakdown(session_factory)] == ["VS Code", "x", "Unknown"]


def test_app_breakdown_respects_the_date_range(session_factory):
    _seed(session_factory, [_dictation("code.exe", day=1), _dictation("chrome.exe", day=20)])

    result = get_app_breakdown(session_factory, start="2026-03-10", end="2026-03-31")

    assert [g["app"] for g in result] == ["Chrome"]


def test_app_breakdown_leaves_out_command_rows(session_factory):
    _seed(session_factory, [_dictation("code.exe"), _dictation(COMMAND_APP, "[nexus] open github")])

    assert get_app_breakdown(session_factory) == [{"app": "VS Code", "count": 1, "words": 2}]


def test_app_breakdown_empty(session_factory):
    assert get_app_breakdown(session_factory) == []


# ---- accuracy and dictionary stats ------------------------------------------


def test_accuracy_counts_corrected_words_per_100(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "one two three four five six seven eight nine ten",
                "corrected_text": "one two three four five six seven eight nine tenth",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 1, 10),
            },
            {
                "text": "a b c d e f g h i j",
                "language": "en",
                "duration_ms": 1000,
                "created_at": datetime(2026, 3, 1, 11),
            },
            {"text": "x y", "language": "en", "duration_ms": 1000, "created_at": datetime(2026, 3, 2, 9)},
        ],
    )

    result = get_accuracy_over_time(session_factory)

    assert result["labels"] == ["2026-03-01", "2026-03-02"]
    assert result["values"] == [5.0, 0.0]
    assert result["corrections"] == 1
    assert result["words"] == 22


def test_accuracy_counts_accepted_suggestions(db, session_factory):
    db.save_transcription("hello world " * 5, "en", 1000)
    db.add_dictionary_suggestions([DictionaryEntry("nexus fox", "NexusVox")])
    suggestion_id = db.list_dictionary_suggestions()[0]["id"]
    db.accept_dictionary_suggestion(suggestion_id)

    result = get_accuracy_over_time(session_factory)

    assert result["corrections"] == 1
    assert result["values"] == [10.0]


def test_accuracy_empty_db(session_factory):
    assert get_accuracy_over_time(session_factory) == {"labels": [], "values": [], "corrections": 0, "words": 0}


def test_accuracy_respects_the_date_range(session_factory):
    _seed(
        session_factory,
        [
            {
                "text": "a b",
                "corrected_text": "a c",
                "language": "en",
                "duration_ms": 1,
                "created_at": datetime(2026, 1, 5),
            },
            {"text": "a b", "language": "en", "duration_ms": 1, "created_at": datetime(2026, 2, 5)},
        ],
    )

    result = get_accuracy_over_time(session_factory, start="2026-02-01", end="2026-02-28")

    assert result["labels"] == ["2026-02-05"]
    assert result["corrections"] == 0


def test_most_corrected_groups_by_phrase(session_factory):
    _seed(
        session_factory,
        [
            {"text": "ask cloud code now", "corrected_text": "ask Claude Code now", "language": "en", "duration_ms": 1},
            {"text": "cloud code again", "corrected_text": "Claude Code again", "language": "en", "duration_ms": 1},
            {"text": "send it", "corrected_text": "send that", "language": "en", "duration_ms": 1},
            {"text": "untouched", "language": "en", "duration_ms": 1},
        ],
    )

    items = get_most_corrected(session_factory)["items"]

    assert items[0] == {"wrong": "cloud code", "right": "Claude Code", "count": 2}
    assert {"wrong": "it", "right": "that", "count": 1} in items


def test_learn_outcomes_survive_in_the_database(db, session_factory):
    for outcome in ("suggested", "suggested", "too_many_changes", "terminal"):
        db.record_learn_event(outcome)
    db.record_learn_event("not_an_outcome")

    result = get_learn_outcomes(session_factory)

    assert [(o["outcome"], o["count"]) for o in result["outcomes"]] == [
        ("suggested", 2),
        ("terminal", 1),
        ("too_many_changes", 1),
    ]
    assert result["watched"] == 3
    assert result["skipped"] == 1
    assert result["suggested"] == 2
    assert result["accepted"] == 0


def test_dictionary_usage_counts_per_entry_and_day(db, session_factory):
    db.add_dictionary_entry("nexus fox", "NexusVox")
    db.add_dictionary_entry("never used", "Unused")
    entry = DictionaryEntry("nexus fox", "NexusVox")
    db.record_dictionary_hits({entry: 2})
    db.record_dictionary_hits({entry: 3})

    result = get_dictionary_usage(session_factory)

    assert result["total_hits"] == 5
    assert result["entries"] == 2
    assert [(i["wrong"], i["hits"]) for i in result["items"]] == [("nexus fox", 5), ("never used", 0)]
    assert result["items"][0]["last_used"] == utc_now().strftime("%Y-%m-%d")
    assert result["items"][1]["last_used"] is None


def test_dictionary_usage_keeps_history_of_deleted_entries(db, session_factory):
    row = db.add_dictionary_entry("nexus fox", "NexusVox")
    db.record_dictionary_hits({DictionaryEntry("Nexus  Fox", "NexusVox"): 1})
    db.delete_dictionary_entry(row["id"])

    items = get_dictionary_usage(session_factory)["items"]

    assert items == [
        {
            "wrong": "nexus fox",
            "right": "NexusVox",
            "hits": 1,
            "last_used": utc_now().strftime("%Y-%m-%d"),
            "removed": True,
        }
    ]


def test_dictionary_usage_respects_the_date_range(db, session_factory):
    db.add_dictionary_entry("nexus fox", "NexusVox")
    db.record_dictionary_hits({DictionaryEntry("nexus fox", "NexusVox"): 4})

    assert get_dictionary_usage(session_factory, start="2000-01-01", end="2000-01-31")["total_hits"] == 0
    assert get_dictionary_usage(session_factory, start="2000-01-01")["total_hits"] == 4


def test_accuracy_ignores_command_rows(session_factory):
    _seed(
        session_factory,
        [
            {"text": "a b c d", "language": "en", "duration_ms": 1},
            {"text": "[os-control] open the browser now", "language": "en", "duration_ms": 1, "app": COMMAND_APP},
        ],
    )

    assert get_accuracy_over_time(session_factory)["words"] == 4


def test_accuracy_keeps_accepted_suggestions_of_a_day_without_dictation(db, session_factory):
    db.save_transcription("one two three four", "en", 1000)
    db.add_dictionary_suggestions([DictionaryEntry("a b", "c")])
    db.accept_dictionary_suggestion(db.list_dictionary_suggestions()[0]["id"])
    with session_factory() as session:
        session.query(Transcription).update({Transcription.created_at: datetime(2000, 1, 1)})
        session.commit()

    result = get_accuracy_over_time(session_factory)

    assert result["corrections"] == 1
    assert result["labels"][0] == "2000-01-01"
    assert result["values"][1:] == [None]


def test_dictionary_usage_shows_the_latest_replacement_of_a_removed_entry(db, session_factory):
    row = db.add_dictionary_entry("nexus fox", "NexusVox")
    db.record_dictionary_hits({DictionaryEntry("nexus fox", "Nexusvox"): 1})
    with session_factory() as session:
        session.query(DictionaryUsageRow).update({DictionaryUsageRow.day: "2000-01-01"})
        session.commit()
    db.record_dictionary_hits({DictionaryEntry("nexus fox", "NexusVox"): 1})
    db.delete_dictionary_entry(row["id"])

    (item,) = get_dictionary_usage(session_factory)["items"]

    assert (item["right"], item["hits"], item["removed"]) == ("NexusVox", 2, True)
