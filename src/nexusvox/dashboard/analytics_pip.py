"""Dashboard analytics: the counts behind Pip's daily speech bubble on Home.

Counts only. Nothing here returns a dictated or transcribed text, a file name or a dictionary entry: the page
turns these numbers into a milestone, a tip or a statistic and never sees what was said.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..storage.models import DictionaryEntryRow, FileTranscription
from .analytics_overview import _TYPING_WPM, _command_row_stats
from .analytics_usage import get_day_totals
from .analytics_week import get_week


def get_pip_facts(session_factory: sessionmaker) -> dict:
    """What Pip may talk about: lifetime counts, the counts of this week and the basis of the time estimate.

    `words` and `dictations` are every dictation so far, counted like every other statistic (command and polish
    rows are no dictations). `meetings` and `uploads` are the saved meeting recordings and transcribed files,
    `dictionary_entries` the user's own words and `voice_commands` the spoken commands recognised so far; the
    page reads a zero as "never used". `week_*` are the numbers Home's *This week* card shows (Monday to
    today), and `time_saved_minutes` is that card's estimate: typing the words at `typing_wpm` words a minute
    minus the time spent speaking.
    """
    totals = get_day_totals(session_factory).values()
    week = get_week(session_factory)
    with session_factory() as session:
        files = dict(session.query(FileTranscription.kind, func.count()).group_by(FileTranscription.kind).all())
        entries = session.query(func.count()).select_from(DictionaryEntryRow).scalar()
    return {
        "words": sum(t["words"] for t in totals),
        "dictations": sum(t["count"] for t in totals),
        "meetings": int(files.get("meeting", 0)),
        "uploads": int(files.get("upload", 0)),
        "dictionary_entries": int(entries),
        "voice_commands": _command_row_stats(session_factory, None, None)["command_count"],
        "week_words": week["words"],
        "week_dictations": week["dictations"],
        "week_time_saved_minutes": week["time_saved_minutes"],
        "typing_wpm": _TYPING_WPM,
    }
