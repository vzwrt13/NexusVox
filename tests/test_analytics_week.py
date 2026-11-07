"""The weekly report (Analytics > Your week) and the words-per-day series behind the Overview chart."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from nexusvox.dashboard.analytics import get_overview, get_week, get_words_per_day
from nexusvox.dashboard.analytics_common import parse_day
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


def test_words_per_day_reads_only_plain_iso_dates(session_factory):
    _dictation(session_factory, 3, 6)

    # "20261101" is a date to fromisoformat but would match no row as a string: it is read as missing instead.
    got = get_words_per_day(session_factory, "20261101", "2026-11-04")

    assert got["labels"] == ["2026-11-03", "2026-11-04"] and got["values"] == [6, 0]


@pytest.mark.parametrize("value", ["20261101", "2026-W45-1", "2026-11-1", "garbage", "", None, "2026-13-01"])
def test_parse_day_rejects_everything_but_a_plain_iso_date(value):
    assert parse_day(value) is None


def test_parse_day_reads_a_plain_iso_date():
    assert parse_day("2026-11-05") == date(2026, 11, 5)


def test_words_per_day_of_an_empty_database_is_empty(session_factory):
    assert get_words_per_day(session_factory) == {"labels": [], "values": []}


def test_words_per_day_reads_a_broken_bound_as_missing(session_factory):
    assert get_words_per_day(session_factory, "garbage", "2026-11-05")["values"] == []


# ---- get_week --------------------------------------------------------------


def test_week_of_an_empty_database(session_factory):
    got = get_week(session_factory, None, today=THURSDAY)

    assert (got["start"], got["end"], got["is_current"]) == (WEEK, "2026-11-08", True)
    assert got["first_week"] is None
    assert [d["words"] for d in got["days"]] == [0] * 7
    assert [d["weekday"] for d in got["days"]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert got["meetings"] == {"count": 0, "seconds": 0}
    assert got["learned"] == {"total": 0, "entries": [], "entries_total": 0, "suggestions_accepted": 0}


def test_week_counts_words_days_and_time_saved(session_factory):
    _dictation(session_factory, 2, 400)
    _dictation(session_factory, 2, 100)
    _dictation(session_factory, 6, 200)
    _dictation(session_factory, 9, 999)  # next week
    _dictation(session_factory, 1, 999)  # Sunday before

    got = get_week(session_factory, "2026-11-04", today=date(2026, 11, 12))  # any day of the week names it

    assert got["start"] == WEEK
    assert got["words"] == 700
    assert got["dictations"] == 3
    assert got["days_dictated"] == 2
    assert [d["words"] for d in got["days"]] == [500, 0, 0, 0, 200, 0, 0]
    assert got["time_saved_minutes"] > 0
    assert got["previous_words"] == 999


def test_the_current_week_counts_only_days_that_have_started(session_factory):
    # Monday to Saturday, a dictation on every day but Wednesday; Sunday has not happened yet.
    for day in (2, 3, 5, 6, 7):
        _dictation(session_factory, day, 100)

    saturday = get_week(session_factory, None, today=date(2026, 11, 7))

    assert (saturday["days_dictated"], saturday["days_counted"]) == (5, 6)
    assert len(saturday["days"]) == 7  # the chart still shows the whole week

    _dictation(session_factory, 4, 100)
    full = get_week(session_factory, None, today=date(2026, 11, 7))
    assert (full["days_dictated"], full["days_counted"]) == (6, 6)


def test_monday_is_one_of_one(session_factory):
    _dictation(session_factory, 2, 100)

    monday = get_week(session_factory, None, today=date(2026, 11, 2))

    assert (monday["days_dictated"], monday["days_counted"]) == (1, 1)
    assert get_week(session_factory, None, today=date(2026, 11, 8))["days_counted"] == 7  # the Sunday itself


def test_a_past_week_is_whole(session_factory):
    _dictation(session_factory, 2, 100)

    got = get_week(session_factory, WEEK, today=date(2026, 11, 12))

    assert (got["is_current"], got["days_counted"], got["days_dictated"]) == (False, 7, 1)


def test_week_counts_meetings_but_not_uploads(session_factory):
    _meeting(session_factory, 3, 30)
    _meeting(session_factory, 4, 15)
    _meeting(session_factory, 4, 90, kind="upload")
    _meeting(session_factory, 12, 60)  # next week

    got = get_week(session_factory, WEEK, today=date(2026, 11, 20))

    assert got["meetings"] == {"count": 2, "seconds": 2700}


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
    assert got["total"] == 7


def test_a_short_meeting_keeps_its_seconds(session_factory):
    _add(
        session_factory,
        FileTranscription(
            text="x",
            language="en",
            duration_ms=25_000,
            original_filename="m.wav",
            kind="meeting",
            created_at=_noon(3),
        ),
    )

    assert get_week(session_factory, WEEK, today=THURSDAY)["meetings"] == {"count": 1, "seconds": 25}


def test_an_accepted_suggestion_that_updated_an_older_entry_still_counts_as_learned(session_factory):
    _add(session_factory, DictionaryEntryRow(wrong="teh", right="the", created_at=_noon(20, 10)))  # an earlier week
    _add(session_factory, LearnEventRow(outcome=ACCEPTED_EVENT, created_at=_noon(4)))

    got = get_week(session_factory, WEEK, today=THURSDAY)["learned"]

    assert (got["entries_total"], got["suggestions_accepted"], got["total"]) == (0, 1, 1)


def test_a_future_week_is_read_as_this_week(session_factory):
    got = get_week(session_factory, "2027-01-01", today=THURSDAY)

    assert got["start"] == WEEK and got["is_current"] is True


def test_a_broken_week_is_read_as_this_week(session_factory):
    assert get_week(session_factory, "not a date", today=THURSDAY)["start"] == WEEK


def test_a_past_week_is_not_current(session_factory):
    got = get_week(session_factory, "2026-10-26", today=THURSDAY)

    assert (got["start"], got["end"], got["is_current"]) == ("2026-10-26", "2026-11-01", False)


def test_first_week_ignores_voice_commands_like_the_rest_of_the_report(session_factory):
    _dictation(session_factory, 22, 30, month=10, app=COMMAND_APP)  # a command three weeks before the dictation
    _dictation(session_factory, 6, 5)

    assert get_week(session_factory, WEEK, today=THURSDAY)["first_week"] == WEEK


def test_week_numbers_match_the_overview_of_the_same_days(session_factory):
    _dictation(session_factory, 2, 40)
    _dictation(session_factory, 5, 60)
    _dictation(session_factory, 5, 500, app=COMMAND_APP)

    got = get_week(session_factory, WEEK, today=THURSDAY)
    overview = get_overview(session_factory, start=WEEK, end="2026-11-08")

    assert (got["words"], got["dictations"], got["time_saved_minutes"]) == (
        overview["total_words"],
        overview["total_transcriptions"],
        overview["time_saved_minutes"],
    )


def test_first_week_is_the_monday_of_the_oldest_dictation_or_meeting(session_factory):
    _dictation(session_factory, 6, 5)
    assert get_week(session_factory, WEEK, today=THURSDAY)["first_week"] == WEEK

    _meeting(session_factory, 22, 10, month=10)  # Thursday 2026-10-22, in the week of 2026-10-19

    assert get_week(session_factory, WEEK, today=THURSDAY)["first_week"] == "2026-10-19"


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
