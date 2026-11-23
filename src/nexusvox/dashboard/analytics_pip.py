"""Dashboard analytics: the counts behind Pip's daily speech bubble on Home.

Counts only. Nothing here returns a dictated or transcribed text, a file name or a dictionary entry: the page
turns these numbers into a milestone, a tip or a statistic and never sees what was said.
"""

from __future__ import annotations

import weakref
from datetime import date

from sqlalchemy import func, or_
from sqlalchemy.orm import sessionmaker

from ..dictation.voice_commands import ALL_STRUCTURAL_INFO
from ..storage.models import DictionaryEntryRow, FileTranscription, Transcription
from .analytics_common import _apply_date_filter
from .analytics_overview import _TYPING_WPM
from .analytics_usage import get_day_totals
from .analytics_week import get_week

# The inline formatting commands a dictation can carry ("new paragraph", "neue Zeile"): they are replaced while the
# text is typed, and the transcript is saved as spoken, so a dictation that used one still contains the phrase. They
# leave no row of their own. "tab" is left out: it is an everyday word, and a hit on it would prove nothing.
_FORMATTING_KEYWORDS = ("new paragraph", "new line")


def _formatting_phrases() -> list[str]:
    """Every spoken form (English keyword and German aliases) of the formatting commands, lower case."""
    phrases: list[str] = []
    for info in ALL_STRUCTURAL_INFO:
        if info["keyword"] in _FORMATTING_KEYWORDS:
            phrases += [info["keyword"], *info["aliases"]]
    return phrases


def _used_formatting_commands(session_factory: sessionmaker) -> int:
    """1 when any saved dictation contains a formatting command, else 0. Only the fact is returned, never the text.

    LIKE is case-insensitive for these ASCII phrases; the query stops at the first hit, so a user of the
    commands pays almost nothing and one who never used them a single scan inside SQLite.
    """
    phrases = _formatting_phrases()
    with session_factory() as session:
        query = session.query(Transcription.id).filter(or_(*(Transcription.text.like(f"%{p}%") for p in phrases)))
        return int(_apply_date_filter(query, None, None).limit(1).first() is not None)


# The words and the week are read from every dictation, so they are kept until a dictation is added or removed (or
# the day changes, which moves the week): Home asks on every visit. Weak keys, so a closed database drops its entry.
_dictation_cache: weakref.WeakKeyDictionary[sessionmaker, tuple[tuple, dict]] = weakref.WeakKeyDictionary()


def _dictation_facts(session_factory: sessionmaker) -> dict:
    with session_factory() as session:
        stamp = (
            date.today(),
            *_apply_date_filter(session.query(func.count(), func.max(Transcription.id)), None, None).one(),
        )
    cached = _dictation_cache.get(session_factory)
    if cached and cached[0] == stamp:
        return cached[1]
    totals = get_day_totals(session_factory).values()
    week = get_week(session_factory)
    facts = {
        "words": sum(t["words"] for t in totals),
        "dictations": sum(t["count"] for t in totals),
        "week_words": week["words"],
        "week_dictations": week["dictations"],
        "week_time_saved_minutes": week["time_saved_minutes"],
    }
    _dictation_cache[session_factory] = (stamp, facts)
    return facts


def get_pip_facts(session_factory: sessionmaker) -> dict:
    """What Pip may talk about: lifetime counts, the counts of this week and the basis of the time estimate.

    `words` and `dictations` are every dictation so far, counted like every other statistic (command and polish
    rows are no dictations). `meetings` and `uploads` are the saved meeting recordings and transcribed files,
    `dictionary_entries` the user's own words and `formatting_commands` is 1 once a dictation used a spoken
    formatting command ("new paragraph", "new line"); the page reads a zero as "never used". `week_*` are the
    numbers Home's *This week* card shows (Monday to today), and `week_time_saved_minutes` is that card's estimate:
    typing the words at `typing_wpm` words a minute minus the time spent speaking.
    """
    with session_factory() as session:
        files = dict(session.query(FileTranscription.kind, func.count()).group_by(FileTranscription.kind).all())
        entries = session.query(func.count()).select_from(DictionaryEntryRow).scalar()
    return {
        **_dictation_facts(session_factory),
        "meetings": int(files.get("meeting", 0)),
        "uploads": int(files.get("upload", 0)),
        "dictionary_entries": int(entries),
        "formatting_commands": _used_formatting_commands(session_factory),
        "typing_wpm": _TYPING_WPM,
    }
