"""Dashboard analytics: the weekly report behind Analytics > Your week.

Everything here is read from rows that already exist (dictations, meetings, dictionary entries, accepted
suggestions); the report adds no tracking of its own.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..storage.models import DictionaryEntryRow, FileTranscription, Transcription
from .analytics_accuracy import get_learn_outcomes
from .analytics_common import _apply_date_filter
from .analytics_overview import get_overview
from .analytics_usage import get_words_per_day

# How many of the new dictionary entries the report names; the rest only counts.
MAX_LEARNED_ENTRIES = 5
# A change of the words against the week before below this share (percent) reads as "about the same".
_SAME_AS_BEFORE_PCT = 5


def week_start(day: date) -> date:
    """The Monday of the week `day` is in."""
    return day - timedelta(days=day.weekday())


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def format_minutes(minutes: float) -> str:
    """Whole minutes as "45 min" or "1 h 5 min"."""
    m = max(0, round(minutes))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _headline(words: int, days: int, saved_minutes: float, previous_words: int, meetings: int) -> str:
    """One sentence for the week: what was dictated, what it saved and how it compares with the week before."""
    if not words:
        if meetings:
            return f"No dictation, but {_plural(meetings, 'meeting')} recorded."
        return "No dictation in this week."
    sentence = f"You dictated {words:,} words on {_plural(days, 'day')}"
    if saved_minutes >= 1:
        sentence += f", saving about {format_minutes(saved_minutes)} of typing"
    sentence += "."
    if previous_words:
        change = round((words - previous_words) * 100 / previous_words)
        if change >= _SAME_AS_BEFORE_PCT:
            sentence += f" That is {change}% more than the week before."
        elif change <= -_SAME_AS_BEFORE_PCT:
            sentence += f" That is {-change}% less than the week before."
        else:
            sentence += " That is about the same as the week before."
    return sentence


def _first_week(session_factory: sessionmaker) -> str | None:
    """The Monday of the week of the oldest dictation or meeting, or None when there is neither."""
    with session_factory() as session:
        dictation = _apply_date_filter(
            session.query(func.min(func.strftime("%Y-%m-%d", Transcription.created_at, "localtime"))), None, None
        ).scalar()
        meeting = (
            session.query(func.min(func.strftime("%Y-%m-%d", FileTranscription.created_at, "localtime")))
            .filter(FileTranscription.kind == "meeting")
            .scalar()
        )
    oldest = min((d for d in (dictation, meeting) if d), default=None)
    return week_start(date.fromisoformat(oldest)).isoformat() if oldest else None


def get_week(session_factory: sessionmaker, week: str | None = None, *, today: date | None = None) -> dict:
    """The report for the Monday-to-Sunday week containing `week` (an ISO date); this week when it is missing.

    Days are local days, like every other statistic. A week in the future is read as the current one.
    """
    today = today or date.today()
    current = week_start(today)
    monday = min(week_start(_parse_day(week) or today), current)
    sunday = monday + timedelta(days=6)
    start, end = monday.isoformat(), sunday.isoformat()
    previous_start = (monday - timedelta(days=7)).isoformat()
    previous_end = (monday - timedelta(days=1)).isoformat()

    overview = get_overview(session_factory, start=start, end=end)
    per_day = get_words_per_day(session_factory, start, end)
    previous_words = sum(get_words_per_day(session_factory, previous_start, previous_end)["values"])
    days = [
        {"date": label, "weekday": date.fromisoformat(label).strftime("%a"), "words": words}
        for label, words in zip(per_day["labels"], per_day["values"], strict=True)
    ]
    days_dictated = sum(1 for d in days if d["words"] > 0)

    with session_factory() as session:
        meeting_query = session.query(func.count(), func.coalesce(func.sum(FileTranscription.duration_ms), 0))
        meeting_count, meeting_ms = (
            _apply_date_filter(meeting_query, start, end, FileTranscription)
            .filter(FileTranscription.kind == "meeting")
            .one()
        )
        entry_query = session.query(DictionaryEntryRow.wrong, DictionaryEntryRow.right)
        entries = _apply_date_filter(entry_query, start, end, DictionaryEntryRow).order_by(DictionaryEntryRow.id).all()
    learned = get_learn_outcomes(session_factory, start=start, end=end)

    words = overview["total_words"]
    saved = overview["time_saved_minutes"]
    return {
        "start": start,
        "end": end,
        "is_current": monday == current,
        "first_week": _first_week(session_factory),
        "headline": _headline(words, days_dictated, saved, previous_words, int(meeting_count)),
        "words": words,
        "dictations": overview["total_transcriptions"],
        "time_saved_minutes": saved,
        "previous_words": previous_words,
        "days": days,
        "days_dictated": days_dictated,
        "meetings": {"count": int(meeting_count), "minutes": round(int(meeting_ms) / 60_000)},
        "learned": {
            "entries": [{"wrong": w, "right": r} for w, r in entries[-MAX_LEARNED_ENTRIES:]],
            "entries_total": len(entries),
            "suggestions_accepted": learned["accepted"],
        },
    }
