"""Dashboard analytics: the weekly report behind Analytics > Your week.

Everything here is read from rows that already exist (dictations, meetings, dictionary entries, accepted
suggestions); the report adds no tracking of its own.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..storage.models import ACCEPTED_EVENT, DictionaryEntryRow, FileTranscription, LearnEventRow, Transcription
from .analytics_common import _apply_date_filter, parse_day
from .analytics_overview import _dictations, time_saved_minutes
from .analytics_usage import day_labels, get_day_totals

# How many of the new dictionary entries the report names; the rest only counts.
MAX_LEARNED_ENTRIES = 5


def week_start(day: date) -> date:
    """The Monday of the week `day` is in."""
    return day - timedelta(days=day.weekday())


def _local_week_start(moment: datetime | None) -> str | None:
    """The Monday (ISO date) of the local week a stored naive-UTC time falls in."""
    if moment is None:
        return None
    return week_start(moment.replace(tzinfo=UTC).astimezone().date()).isoformat()


def _first_week(session_factory: sessionmaker) -> str | None:
    """The Monday of the week of the oldest dictation or meeting, or None when there is neither.

    Dictations are counted as the rest of the report counts them (no polish and no command rows), so the
    week switcher never leads back to weeks the report shows as empty.
    """
    with session_factory() as session:
        dictation = _dictations(session.query(func.min(Transcription.created_at)), None, None).scalar()
        meeting = (
            session.query(func.min(FileTranscription.created_at)).filter(FileTranscription.kind == "meeting").scalar()
        )
    return min((w for w in (_local_week_start(dictation), _local_week_start(meeting)) if w), default=None)


def get_week(session_factory: sessionmaker, week: str | None = None, *, today: date | None = None) -> dict:
    """The report for the Monday-to-Sunday week containing `week` (a `YYYY-MM-DD` date); this week when it is
    missing or unreadable.

    Days are local days, like every other statistic. A week in the future is read as the current one. The
    sentence that tells the week in words is the page's: it formats the numbers the way the browser does.
    """
    today = today or date.today()
    current = week_start(today)
    monday = min(week_start(parse_day(week) or today), current)
    sunday = monday + timedelta(days=6)
    start, end = monday.isoformat(), sunday.isoformat()
    previous = monday - timedelta(days=7)

    # The week and the one before it in one query: words, dictations and time saved all come from the day rows.
    totals = get_day_totals(session_factory, previous.isoformat(), end)
    labels = day_labels(monday, sunday)
    days = [
        {
            "date": label,
            "weekday": date.fromisoformat(label).strftime("%a"),
            "words": totals[label]["words"] if label in totals else 0,
        }
        for label in labels
    ]
    # Only days that have started count: on a Saturday the week has six days so far, not seven. A past week
    # is whole. Weeks stay Monday to Sunday.
    days_counted = (today - monday).days + 1 if monday == current else 7
    this_week = [totals[label] for label in labels if label in totals]
    words = sum(d["words"] for d in this_week)
    duration_ms = sum(d["duration_ms"] for d in this_week)
    previous_words = sum(t["words"] for day, t in totals.items() if day < start)

    with session_factory() as session:
        meeting_query = session.query(func.count(), func.coalesce(func.sum(FileTranscription.duration_ms), 0))
        meeting_count, meeting_ms = (
            _apply_date_filter(meeting_query, start, end, FileTranscription)
            .filter(FileTranscription.kind == "meeting")
            .one()
        )
        entry_query = session.query(DictionaryEntryRow.wrong, DictionaryEntryRow.right)
        entries = _apply_date_filter(entry_query, start, end, DictionaryEntryRow).order_by(DictionaryEntryRow.id).all()
        accepted_query = session.query(func.count()).filter(LearnEventRow.outcome == ACCEPTED_EVENT)
        accepted = _apply_date_filter(accepted_query, start, end, LearnEventRow).scalar()

    return {
        "start": start,
        "end": end,
        "is_current": monday == current,
        "first_week": _first_week(session_factory),
        "words": words,
        "dictations": sum(d["count"] for d in this_week),
        "time_saved_minutes": round(time_saved_minutes(words, duration_ms), 1),
        "previous_words": previous_words,
        "days": days,
        "days_dictated": sum(1 for d in days[:days_counted] if d["words"] > 0),
        "days_counted": days_counted,
        "meetings": {"count": int(meeting_count), "seconds": round(int(meeting_ms) / 1000)},
        "learned": {
            # An accepted suggestion only makes a new entry when its phrase is not in the dictionary yet; one
            # that updated an older entry is still something learned, so the total is never below it.
            "total": max(len(entries), accepted),
            "entries": [{"wrong": w, "right": r} for w, r in entries[-MAX_LEARNED_ENTRIES:]],
            "entries_total": len(entries),
            "suggestions_accepted": accepted,
        },
    }
