"""The weekly report (Analytics > Your week) and the words-per-day series behind the Overview chart."""

from __future__ import annotations

from datetime import date, datetime

from nexusvox.dashboard.analytics import get_week, get_words_per_day
from nexusvox.dashboard.analytics_week import _headline, format_minutes
from nexusvox.storage.models import (
    ACCEPTED_EVENT,
    COMMAND_APP,
    DictionaryEntryRow,
    FileTranscription,
    LearnEventRow,
    Transcription,
)

THURSDAY = date(2026, 11, 5)  # the week of Monday 2026-11-02
WEEK = "2026-11-02"


def _noon(day: int, month: int = 11) -> datetime:
    return datetime(2026, month, day, 12, 0)  # midday UTC: the same local day in every time zone


def _dictation(session_factory, day: int, words: int, *, month: int = 11, app: str | None = None):
    with session_factory() as session:
        session.add(
            Transcription(
                text=" ".join(["word"] * words),
                language="en",
                duration_ms=words * 400,
                created_at=_noon(day, month),
                app=app,
            )
        )
        session.commit()


def _add(session_factory, row):
    with session_factory() as session:
        session.add(row)
        session.commit()


def _meeting(session_factory, day: int, minutes: int, kind: str = "meeting", month: int = 11):
    _add(
        session_factory,
        FileTranscription(
            text="notes",
            language="en",
            duration_ms=minutes * 60_000,
            original_filename="m.wav",
            kind=kind,
            created_at=_noon(day, month),
        ),
    )


# ---- get_words_per_day -----------------------------------------------------


def test_words_per_day_lists_every_day_and_fills_the_quiet_ones(session_factory):
    _dictation(session_factory, 2, 5)
    _dictation(session_factory, 2, 3)
    _dictation(session_factory, 4, 10)

    got = get_words_per_day(session_factory, "2026-11-02", "2026-11-05")

    assert got["labels"] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    assert got["values"] == [8, 0, 10, 0]


def test_words_per_day_leaves_out_command_rows(session_factory):
    _dictation(session_factory, 2, 4)
    _dictation(session_factory, 2, 30, app=COMMAND_APP)

    assert get_words_per_day(session_factory, "2026-11-02", "2026-11-02")["values"] == [4]


def test_words_per_day_without_a_start_begins_with_the_first_dictation(session_factory):
    _dictation(session_factory, 28, 2, month=10)

    got = get_words_per_day(session_factory, None, "2026-10-30")

    assert got["labels"] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert got["values"] == [2, 0, 0]


def test_words_per_day_of_an_empty_database_is_empty(session_factory):
    assert get_words_per_day(session_factory) == {"labels": [], "values": []}


def test_words_per_day_reads_a_broken_bound_as_missing(session_factory):
    assert get_words_per_day(session_factory, "garbage", "2026-11-05")["values"] == []


# ---- get_week --------------------------------------------------------------


def test_week_of_an_empty_database(session_factory):
    got = get_week(session_factory, None, today=THURSDAY)

    assert (got["start"], got["end"], got["is_current"]) == (WEEK, "2026-11-08", True)
    assert got["first_week"] is None
    assert got["headline"] == "No dictation in this week."
    assert [d["words"] for d in got["days"]] == [0] * 7
    assert [d["weekday"] for d in got["days"]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert got["meetings"] == {"count": 0, "minutes": 0}
    assert got["learned"] == {"entries": [], "entries_total": 0, "suggestions_accepted": 0}


def test_week_counts_words_days_and_time_saved(session_factory):
    _dictation(session_factory, 2, 400)
    _dictation(session_factory, 2, 100)
    _dictation(session_factory, 6, 200)
    _dictation(session_factory, 9, 999)  # next week
    _dictation(session_factory, 1, 999)  # Sunday before

    got = get_week(session_factory, "2026-11-04", today=THURSDAY)  # any day of the week names it

    assert got["start"] == WEEK
    assert got["words"] == 700
    assert got["dictations"] == 3
    assert got["days_dictated"] == 2
    assert [d["words"] for d in got["days"]] == [500, 0, 0, 0, 200, 0, 0]
    assert got["time_saved_minutes"] > 0
    assert got["previous_words"] == 999


def test_week_counts_meetings_but_not_uploads(session_factory):
    _meeting(session_factory, 3, 30)
    _meeting(session_factory, 4, 15)
    _meeting(session_factory, 4, 90, kind="upload")
    _meeting(session_factory, 12, 60)  # next week

    got = get_week(session_factory, WEEK, today=date(2026, 11, 20))

    assert got["meetings"] == {"count": 2, "minutes": 45}


def test_week_lists_the_newest_dictionary_entries_and_accepted_suggestions(session_factory):
    for n in range(7):
        _add(session_factory, DictionaryEntryRow(wrong=f"wrong{n}", right=f"right{n}", created_at=_noon(3)))
    _add(session_factory, DictionaryEntryRow(wrong="old", right="Old", created_at=_noon(20, 10)))
    _add(session_factory, LearnEventRow(outcome=ACCEPTED_EVENT, created_at=_noon(4)))
    _add(session_factory, LearnEventRow(outcome=ACCEPTED_EVENT, created_at=_noon(12)))

    got = get_week(session_factory, WEEK, today=THURSDAY)["learned"]

    assert got["entries_total"] == 7
    assert [e["wrong"] for e in got["entries"]] == ["wrong2", "wrong3", "wrong4", "wrong5", "wrong6"]
    assert got["suggestions_accepted"] == 1


def test_a_future_week_is_read_as_this_week(session_factory):
    got = get_week(session_factory, "2027-01-01", today=THURSDAY)

    assert got["start"] == WEEK and got["is_current"] is True


def test_a_broken_week_is_read_as_this_week(session_factory):
    assert get_week(session_factory, "not a date", today=THURSDAY)["start"] == WEEK


def test_a_past_week_is_not_current(session_factory):
    got = get_week(session_factory, "2026-10-26", today=THURSDAY)

    assert (got["start"], got["end"], got["is_current"]) == ("2026-10-26", "2026-11-01", False)


def test_first_week_is_the_monday_of_the_oldest_dictation_or_meeting(session_factory):
    _dictation(session_factory, 6, 5)
    assert get_week(session_factory, WEEK, today=THURSDAY)["first_week"] == WEEK

    _meeting(session_factory, 22, 10, month=10)  # Thursday 2026-10-22, in the week of 2026-10-19

    assert get_week(session_factory, WEEK, today=THURSDAY)["first_week"] == "2026-10-19"


def test_headline_compares_with_the_week_before():
    assert _headline(1200, 4, 22, 1000, 0) == (
        "You dictated 1,200 words on 4 days, saving about 22 min of typing. That is 20% more than the week before."
    )
    assert _headline(800, 1, 0.4, 1000, 0) == "You dictated 800 words on 1 day. That is 20% less than the week before."
    assert _headline(1020, 2, 65, 1000, 0).endswith("That is about the same as the week before.")
    assert "week before" not in _headline(500, 2, 5, 0, 0)


def test_headline_without_dictation_mentions_meetings():
    assert _headline(0, 0, 0, 900, 2) == "No dictation, but 2 meetings recorded."
    assert _headline(0, 0, 0, 900, 1) == "No dictation, but 1 meeting recorded."


def test_format_minutes():
    assert [format_minutes(m) for m in (0, 44.6, 60, 125)] == ["0 min", "45 min", "1 h 0 min", "2 h 5 min"]


# ---- routes ----------------------------------------------------------------


def test_week_endpoint(flask_client):
    resp = flask_client.get("/api/week")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["is_current"] is True and len(data["days"]) == 7


def test_week_endpoint_takes_a_week_and_survives_garbage(flask_client):
    assert flask_client.get("/api/week?week=2026-01-07").get_json()["start"] == "2026-01-05"
    assert flask_client.get("/api/week?week=zzz").status_code == 200


def test_words_per_day_endpoint(flask_client):
    resp = flask_client.get("/api/words-per-day?start=2026-01-01&end=2026-01-03")

    assert resp.get_json() == {"labels": ["2026-01-01", "2026-01-02", "2026-01-03"], "values": [0, 0, 0]}
