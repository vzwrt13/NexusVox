"""Dashboard analytics: corrections, learning from edits and dictionary use."""

from __future__ import annotations

import difflib
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..dictation.dictionary import suggest_entries
from ..dictation.edit_watch import OUTCOMES, WATCHED_KEYS
from ..storage.models import (
    ACCEPTED_EVENT,
    DictionaryEntryRow,
    DictionaryUsageRow,
    LearnEventRow,
    Transcription,
)
from ..storage.vocabulary import usage_key
from .analytics_common import _apply_date_filter, period_bucket


def _word_edits(original: str, corrected: str) -> int:
    """Words changed between a transcript and its correction: the larger side of every differing stretch."""
    a, b = original.split(), corrected.split()
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal")


def get_accuracy_over_time(
    session_factory: sessionmaker, period: str = "day", *, start: str | None = None, end: str | None = None
) -> dict:
    """Corrections per 100 dictated words, by day, week or month.

    A correction is a word the user changed in a reviewed transcript (`corrected_text`) or a suggestion
    accepted in the dashboard. Periods without a dictation are left out.
    """
    period_col = period_bucket(period, Transcription.created_at)

    with session_factory() as session:
        query = session.query(period_col, Transcription.text, Transcription.corrected_text)
        rows = _apply_date_filter(query, start, end, dictations=True).all()
        event_period = period_bucket(period, LearnEventRow.created_at)
        event_query = session.query(event_period, func.count()).filter(LearnEventRow.outcome == ACCEPTED_EVENT)
        accepted = dict(_apply_date_filter(event_query, start, end, LearnEventRow).group_by(event_period).all())

    words: Counter[str] = Counter()
    corrections: Counter[str] = Counter()
    for bucket, text, corrected in rows:
        words[bucket] += len(text.split())
        if corrected:
            corrections[bucket] += _word_edits(text, corrected)
    for bucket, count in accepted.items():
        corrections[bucket] += count

    # A period with only accepted suggestions has no rate (no words), but its corrections still count.
    labels = sorted(b for b in set(words) | set(corrections) if words[b] or corrections[b])
    return {
        "labels": labels,
        "values": [round(corrections[b] * 100 / words[b], 1) if words[b] else None for b in labels],
        "corrections": sum(corrections.values()),
        "words": sum(words.values()),
    }


def get_most_corrected(
    session_factory: sessionmaker, n: int = 10, *, start: str | None = None, end: str | None = None
) -> dict:
    """The phrases the user corrected most often in reviewed transcripts, as transcribed -> as corrected."""
    with session_factory() as session:
        query = session.query(Transcription.text, Transcription.corrected_text)
        rows = _apply_date_filter(query, start, end).filter(Transcription.corrected_text.isnot(None)).all()

    counter: Counter[tuple[str, str]] = Counter()
    for text, corrected in rows:
        for entry in suggest_entries(text, corrected):
            counter[(entry.wrong.lower(), entry.right)] += 1
    return {
        "items": [{"wrong": wrong, "right": right, "count": count} for (wrong, right), count in counter.most_common(n)]
    }


def get_learn_outcomes(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """How watched pastes ended for learning from edits, plus the suggestions the user accepted.

    `outcomes` lists every outcome that was seen, most frequent first, with the sentence the dashboard shows;
    skipped pastes (terminal, password field, ...) count in `skipped` but not in `watched`.
    """
    with session_factory() as session:
        query = session.query(LearnEventRow.outcome, func.count())
        rows = _apply_date_filter(query, start, end, LearnEventRow).group_by(LearnEventRow.outcome).all()

    counts = dict(rows)
    outcomes = [
        {"outcome": key, "message": OUTCOMES[key], "count": count}
        for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if key in OUTCOMES
    ]
    watched = sum(c for k, c in counts.items() if k in WATCHED_KEYS)
    return {
        "outcomes": outcomes,
        "watched": watched,
        "skipped": sum(c for k, c in counts.items() if k in OUTCOMES) - watched,
        "suggested": counts.get("suggested", 0),
        "accepted": counts.get(ACCEPTED_EVENT, 0),
    }


def get_dictionary_usage(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Every dictionary entry with how often it fired in the range; entries that never fired show 0.

    Entries that were deleted keep their history and show as `removed`, with the replacement they had last.
    """
    with session_factory() as session:
        query = session.query(DictionaryUsageRow.wrong, DictionaryUsageRow.right, DictionaryUsageRow.hits)
        query = query.add_columns(DictionaryUsageRow.day)
        if start:
            query = query.filter(DictionaryUsageRow.day >= start)
        if end:
            query = query.filter(DictionaryUsageRow.day <= end)
        usage_rows = query.order_by(DictionaryUsageRow.day).all()  # oldest first: the last row wins `right`
        entries = session.query(DictionaryEntryRow.wrong, DictionaryEntryRow.right).all()

    used: dict[str, tuple[str, int, str]] = {}
    for wrong, right, hits, day in usage_rows:
        _, total, _ = used.get(wrong, (right, 0, day))
        used[wrong] = (right, total + hits, day)

    items = []
    current = set()
    for wrong, right in entries:
        key = usage_key(wrong)
        current.add(key)
        _, hits, last = used.get(key, (right, 0, None))
        items.append({"wrong": wrong, "right": right, "hits": hits, "last_used": last, "removed": False})
    for key, (right, hits, last) in used.items():
        if key not in current:
            items.append({"wrong": key, "right": right, "hits": hits, "last_used": last, "removed": True})
    items.sort(key=lambda i: (-i["hits"], i["wrong"].lower()))
    return {"items": items, "total_hits": sum(i["hits"] for i in items), "entries": len(entries)}
