"""SQLAlchemy query functions for dashboard analytics."""

from __future__ import annotations

import difflib
import json
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..dictionary import suggest_entries, usage_key
from ..edit_watch import OUTCOMES, WATCHED_KEYS
from ..models import (
    ACCEPTED_EVENT,
    COMMAND_APP,
    DictionaryEntryRow,
    DictionaryUsageRow,
    FileTranscription,
    LearnEventRow,
    Transcription,
)

# Assumed average typing speed for "time saved" calculation.
_TYPING_WPM = 40


def _apply_date_filter(query, start: str | None, end: str | None, model=Transcription):
    """Filter a query by optional ISO date bounds (inclusive)."""
    if start:
        query = query.filter(model.created_at >= start)
    if end:
        query = query.filter(model.created_at < end + "T23:59:59")
    return query


def get_overview(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Totals, WPM, duration, latency, and estimated time saved."""
    with session_factory() as session:
        query = session.query(Transcription.text, Transcription.duration_ms)
        rows = _apply_date_filter(query, start, end).all()

    if not rows:
        return {
            "total_transcriptions": 0,
            "total_words": 0,
            "avg_duration_ms": 0,
            "longest_duration_ms": 0,
            "longest_words": 0,
            "avg_wpm": 0,
            "min_wpm": 0,
            "max_wpm": 0,
            "time_saved_minutes": 0,
            "avg_transcribe_ms": None,
            "avg_latency_ms": None,
            "avg_rtf": None,
            "translated_count": 0,
            "avg_translate_ms": None,
            "translate_failures": 0,
            **_EMPTY_CLEANUP,
            **_EMPTY_POLISH,
        }

    total = len(rows)
    total_duration_ms = sum(r.duration_ms for r in rows)
    word_counts = [len(r.text.split()) for r in rows]
    total_words = sum(word_counts)

    avg_duration_ms = total_duration_ms / total
    avg_wpm = (total_words / (total_duration_ms / 60_000)) if total_duration_ms > 0 else 0
    # Time it would take to type those words minus time spent recording.
    time_saved_min = (total_words / _TYPING_WPM) - (total_duration_ms / 60_000)

    # Per-transcription WPM for min/max.
    per_wpm = []
    for r in rows:
        if r.duration_ms > 0:
            per_wpm.append(len(r.text.split()) / (r.duration_ms / 60_000))

    # Timing: how long the transcript took to arrive, how long until it was typed,
    # and the real-time factor (transcribe time / audio length; below 1 is faster
    # than real time). Only rows recorded since these columns exist count.
    with session_factory() as session:
        timing_query = session.query(
            func.avg(Transcription.transcribe_ms),
            func.avg(Transcription.latency_ms),
            func.sum(Transcription.transcribe_ms),
            func.sum(Transcription.duration_ms).filter(Transcription.transcribe_ms.isnot(None)),
        )
        avg_transcribe_ms, avg_latency_ms, timed_transcribe_ms, timed_duration_ms = _apply_date_filter(
            timing_query, start, end
        ).one()
    avg_rtf = timed_transcribe_ms / timed_duration_ms if timed_transcribe_ms and timed_duration_ms else None

    # Translate-before-inject: how often it ran, what it cost, how often it fell back.
    with session_factory() as session:
        tr_query = session.query(
            func.sum(Transcription.translated),
            func.avg(Transcription.translate_ms),
            func.count(Transcription.translate_error),
        )
        translated_count, avg_translate_ms, translate_failures = _apply_date_filter(tr_query, start, end).one()

    # Auto cleanup: how often the model's pass was used, and how it ended when it was not.
    with session_factory() as session:
        cl_query = session.query(Transcription.cleanup_status, func.count(), func.avg(Transcription.cleanup_ms))
        cleanup_rows = _apply_date_filter(cl_query, start, end).filter(Transcription.cleanup_status.isnot(None))
        cleanup_rows = cleanup_rows.group_by(Transcription.cleanup_status).all()
    cleanup_counts = {status: count for status, count, _ in cleanup_rows}
    timed = [(count, avg) for _, count, avg in cleanup_rows if avg is not None]
    timed_count = sum(count for count, _ in timed)
    avg_cleanup_ms = sum(count * avg for count, avg in timed) / timed_count if timed_count else None

    polish = _polish_stats(session_factory, start, end)

    return {
        "total_transcriptions": total,
        "total_words": total_words,
        "avg_duration_ms": round(avg_duration_ms),
        "longest_duration_ms": max(r.duration_ms for r in rows),
        "longest_words": max(word_counts),
        "avg_wpm": round(avg_wpm, 1),
        "min_wpm": round(min(per_wpm), 1) if per_wpm else 0,
        "max_wpm": round(max(per_wpm), 1) if per_wpm else 0,
        "time_saved_minutes": round(max(time_saved_min, 0), 1),
        "avg_transcribe_ms": round(avg_transcribe_ms) if avg_transcribe_ms is not None else None,
        "avg_latency_ms": round(avg_latency_ms) if avg_latency_ms is not None else None,
        "avg_rtf": round(avg_rtf, 2) if avg_rtf is not None else None,
        "translated_count": int(translated_count or 0),
        "avg_translate_ms": round(avg_translate_ms) if avg_translate_ms is not None else None,
        "translate_failures": int(translate_failures or 0),
        "cleanup_applied": cleanup_counts.get("applied", 0),
        "cleanup_unchanged": cleanup_counts.get("unchanged", 0),
        "cleanup_rejected": cleanup_counts.get("rejected", 0),
        "cleanup_timeouts": cleanup_counts.get("timeout", 0),
        "cleanup_failed": cleanup_counts.get("failed", 0),
        "avg_cleanup_ms": round(avg_cleanup_ms) if avg_cleanup_ms is not None else None,
        **polish,
    }


_EMPTY_CLEANUP = {
    "cleanup_applied": 0,
    "cleanup_unchanged": 0,
    "cleanup_rejected": 0,
    "cleanup_timeouts": 0,
    "cleanup_failed": 0,
    "avg_cleanup_ms": None,
}


_EMPTY_POLISH = {
    "polish_applied": 0,
    "polish_extension": 0,
    "polish_timeouts": 0,
    "polish_failed": 0,
    "avg_polish_ms": None,
}


def _polish_stats(session_factory: sessionmaker, start: str | None, end: str | None) -> dict:
    """Polish on selected text: how often it ran, how it ended, and the model's average time.

    Rows from before `polish_status` existed carry only their label, and were saved only when the
    polish went through, so a `[polish] ...` or `[extension] ...` command row without a status counts as
    applied or handled by an extension.
    """
    with session_factory() as session:
        query = session.query(Transcription.polish_status, Transcription.polish_ms, Transcription.text)
        query = _apply_date_filter(query, start, end).filter(
            Transcription.polish_status.isnot(None)
            | (
                (Transcription.app == COMMAND_APP)
                & (Transcription.text.startswith("[polish] ") | Transcription.text.startswith("[extension] "))
            )
        )
        rows = query.all()
    counts = {"applied": 0, "extension": 0, "timeout": 0, "failed": 0}
    times = []
    for status, ms, text in rows:
        if status is None:
            status = "extension" if text.startswith("[extension] ") else "applied"
        if status in counts:
            counts[status] += 1
        if ms is not None:
            times.append(ms)
    return {
        "polish_applied": counts["applied"],
        "polish_extension": counts["extension"],
        "polish_timeouts": counts["timeout"],
        "polish_failed": counts["failed"],
        "avg_polish_ms": round(sum(times) / len(times)) if times else None,
    }


def _speaker_count(words_json: str | None) -> int:
    """Distinct speakers in a stored `words` JSON; 0 when there is no word list or no speaker detection ran."""
    try:
        words = json.loads(words_json) if words_json else []
        return len({w["speaker"] for w in words if w.get("speaker") is not None})
    except (ValueError, TypeError, AttributeError):
        return 0


def get_upload_overview(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """File uploads (not meetings): count, audio hours, processing time, real-time factor and speakers.

    Uploads saved before processing time was measured still count and add their audio; only the timing
    averages leave them out. Speakers per upload averages over the uploads that had speaker detection.
    """
    with session_factory() as session:
        query = session.query(FileTranscription.duration_ms, FileTranscription.processing_ms, FileTranscription.words)
        query = _apply_date_filter(query, start, end, FileTranscription).filter(FileTranscription.kind == "upload")
        rows = query.all()

    timed = [r for r in rows if r.processing_ms is not None]
    processing_ms = sum(r.processing_ms for r in timed)
    timed_audio_ms = sum(r.duration_ms for r in timed)
    speakers = [n for n in (_speaker_count(r.words) for r in rows) if n]
    return {
        "upload_count": len(rows),
        "upload_audio_hours": round(sum(r.duration_ms for r in rows) / 3_600_000, 2),
        "avg_upload_processing_ms": round(processing_ms / len(timed)) if timed else None,
        "upload_rtf": round(processing_ms / timed_audio_ms, 2) if timed and timed_audio_ms else None,
        "avg_upload_speakers": round(sum(speakers) / len(speakers), 1) if speakers else None,
    }


def get_transcriptions_over_time(
    session_factory: sessionmaker, period: str = "day", *, start: str | None = None, end: str | None = None
) -> dict:
    """Transcription counts grouped by day, week, or month."""
    fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}.get(period, "%Y-%m-%d")

    with session_factory() as session:
        query = session.query(
            func.strftime(fmt, Transcription.created_at).label("period"),
            func.count().label("count"),
        )
        rows = _apply_date_filter(query, start, end).group_by("period").order_by("period").all()

    return {"labels": [r.period for r in rows], "values": [r.count for r in rows]}


def get_language_distribution(
    session_factory: sessionmaker, *, start: str | None = None, end: str | None = None
) -> dict:
    """Count of transcriptions by language."""
    with session_factory() as session:
        query = session.query(
            Transcription.language,
            func.count().label("count"),
        )
        rows = _apply_date_filter(query, start, end).group_by(Transcription.language).all()

    return {"labels": [r.language.upper() for r in rows], "values": [r.count for r in rows]}


def get_top_words(
    session_factory: sessionmaker, n: int = 20, *, start: str | None = None, end: str | None = None
) -> dict:
    """Most frequent words across all transcriptions."""
    with session_factory() as session:
        query = session.query(Transcription.text)
        texts = _apply_date_filter(query, start, end).all()

    counter: Counter[str] = Counter()
    for (text,) in texts:
        for word in text.lower().split():
            cleaned = word.strip(".,!?;:\"'()-")
            if len(cleaned) > 1:
                counter[cleaned] += 1

    most_common = counter.most_common(n)
    return {"labels": [w for w, _ in most_common], "values": [c for _, c in most_common]}


def get_peak_usage_hours(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Transcription count by hour of day (0-23)."""
    with session_factory() as session:
        query = session.query(
            func.strftime("%H", Transcription.created_at).label("hour"),
            func.count().label("count"),
        )
        rows = _apply_date_filter(query, start, end).group_by("hour").order_by("hour").all()

    # Fill in missing hours with 0.
    hour_map = {r.hour: r.count for r in rows}
    labels = [f"{h:02d}" for h in range(24)]
    values = [hour_map.get(f"{h:02d}", 0) for h in range(24)]

    return {"labels": labels, "values": values}


def get_activity_heatmap(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Day-of-week (0=Sun) x hour-of-day grid of transcription counts."""
    with session_factory() as session:
        query = session.query(
            func.strftime("%w", Transcription.created_at).label("dow"),
            func.strftime("%H", Transcription.created_at).label("hour"),
            func.count().label("count"),
        )
        rows = _apply_date_filter(query, start, end).group_by("dow", "hour").all()

    # Build 7x24 grid (days x hours).
    grid = [[0] * 24 for _ in range(7)]
    for r in rows:
        grid[int(r.dow)][int(r.hour)] = r.count

    return {
        "days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "hours": list(range(24)),
        "grid": grid,
    }


def get_flagged_transcriptions(session_factory: sessionmaker, limit: int = 50) -> list[dict]:
    """Return flagged transcriptions for the Edit tab."""
    with session_factory() as session:
        rows = (
            session.query(Transcription)
            .filter(Transcription.flagged == 1)
            .order_by(Transcription.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "text": r.text,
                "corrected_text": r.corrected_text,
                "confidence": round(r.confidence, 4) if r.confidence is not None else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "language": r.language,
                "audio_path": r.audio_path,
            }
            for r in rows
        ]


def get_unreviewed_transcriptions(
    session_factory: sessionmaker, limit: int = 50, *, before_id: int | None = None
) -> dict:
    """Return a page of unreviewed transcriptions that have audio, newest first.

    ``before_id`` is a cursor: only rows with a smaller id are returned, so the
    client can page through the backlog by passing the last id it has seen.
    ``remaining`` counts the unreviewed rows older than the returned page.
    """
    with session_factory() as session:
        base = session.query(Transcription).filter(Transcription.reviewed == 0, Transcription.audio_path.isnot(None))
        if before_id is not None:
            base = base.filter(Transcription.id < before_id)
        rows = base.order_by(Transcription.created_at.desc(), Transcription.id.desc()).limit(limit).all()
        remaining = base.filter(Transcription.id < rows[-1].id).count() if rows else 0
        return {
            "items": [
                {
                    "id": r.id,
                    "text": r.text,
                    "corrected_text": r.corrected_text,
                    "confidence": round(r.confidence, 4) if r.confidence is not None else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "language": r.language,
                    "audio_path": r.audio_path,
                    "flagged": r.flagged,
                    "duration_ms": r.duration_ms,
                }
                for r in rows
            ],
            "remaining": remaining,
        }


def get_latency_over_time(
    session_factory: sessionmaker, period: str = "day", *, start: str | None = None, end: str | None = None
) -> dict:
    """Average transcribe and injection latency grouped by time period."""
    fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}.get(period, "%Y-%m-%d")

    with session_factory() as session:
        query = session.query(
            func.strftime(fmt, Transcription.created_at).label("period"),
            func.avg(Transcription.transcribe_ms).label("transcribe_ms"),
            func.avg(Transcription.latency_ms).label("latency_ms"),
        ).filter(Transcription.transcribe_ms.isnot(None))

        rows = _apply_date_filter(query, start, end).group_by("period").order_by("period").all()

    return {
        "labels": [r.period for r in rows],
        "transcribe_ms": [round(r.transcribe_ms) for r in rows],
        "latency_ms": [round(r.latency_ms) if r.latency_ms is not None else None for r in rows],
    }


def get_model_breakdown(
    session_factory: sessionmaker, *, start: str | None = None, end: str | None = None
) -> list[dict]:
    """Per-model count, WPM, duration, and transcribe latency, busiest model first."""
    with session_factory() as session:
        query = session.query(
            Transcription.model,
            Transcription.text,
            Transcription.duration_ms,
            Transcription.transcribe_ms,
        )
        rows = _apply_date_filter(query, start, end).all()

    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.model or "unknown", []).append(r)

    out = []
    for model, items in groups.items():
        words = sum(len(r.text.split()) for r in items)
        duration_ms = sum(r.duration_ms for r in items)
        timed = [r.transcribe_ms for r in items if r.transcribe_ms is not None]
        out.append(
            {
                "model": model,
                "count": len(items),
                "words": words,
                "avg_wpm": round(words / (duration_ms / 60_000), 1) if duration_ms > 0 else 0,
                "avg_duration_ms": round(duration_ms / len(items)),
                "avg_transcribe_ms": round(sum(timed) / len(timed)) if timed else None,
            }
        )
    out.sort(key=lambda m: m["count"], reverse=True)
    return out


# Friendly names for common apps, keyed by lower-case process file name. Anything else is "Other".
APP_NAMES = {
    "outlook.exe": "Outlook",
    "olk.exe": "Outlook",
    "thunderbird.exe": "Thunderbird",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "code.exe": "VS Code",
    "cursor.exe": "Cursor",
    "devenv.exe": "Visual Studio",
    "pycharm64.exe": "PyCharm",
    "idea64.exe": "IntelliJ IDEA",
    "notepad.exe": "Notepad",
    "notepad++.exe": "Notepad++",
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "onenote.exe": "OneNote",
    "teams.exe": "Teams",
    "ms-teams.exe": "Teams",
    "slack.exe": "Slack",
    "discord.exe": "Discord",
    "telegram.exe": "Telegram",
    "whatsapp.exe": "WhatsApp",
    "signal.exe": "Signal",
    "zoom.exe": "Zoom",
    "obsidian.exe": "Obsidian",
    "notion.exe": "Notion",
    "windowsterminal.exe": "Windows Terminal",
    "wt.exe": "Windows Terminal",
    "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell",
    "cmd.exe": "Command Prompt",
    "explorer.exe": "File Explorer",
}
UNKNOWN_APP = "Unknown"
OTHER_APP = "Other"


def app_label(process: str | None) -> str:
    """The name shown for a stored process name: friendly when common, "Other" when not, "Unknown" when unrecorded.

    Process names are stored lower-case (`App._paste`), so they are looked up as they are.
    """
    if not process:
        return UNKNOWN_APP
    return APP_NAMES.get(process, OTHER_APP)


def get_app_breakdown(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> list[dict]:
    """Dictations and words per target app, busiest first; "Other" and "Unknown" always come last.

    Process names that share a friendly name (`olk.exe` and `outlook.exe`) are one row. Rows that ran a command
    instead of typing text are left out.
    """
    with session_factory() as session:
        query = session.query(Transcription.app, Transcription.text)
        query = query.filter(Transcription.app.is_(None) | (Transcription.app != COMMAND_APP))
        rows = _apply_date_filter(query, start, end).all()

    groups: dict[str, dict] = {}
    for r in rows:
        label = app_label(r.app)
        group = groups.setdefault(label, {"app": label, "count": 0, "words": 0})
        group["count"] += 1
        group["words"] += len(r.text.split())
    tail = {OTHER_APP: 1, UNKNOWN_APP: 2}
    return sorted(groups.values(), key=lambda g: (tail.get(g["app"], 0), -g["count"], g["app"]))


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
    fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}.get(period, "%Y-%m-%d")
    period_col = func.strftime(fmt, Transcription.created_at)

    with session_factory() as session:
        query = session.query(period_col, Transcription.text, Transcription.corrected_text)
        query = query.filter(Transcription.app.is_(None) | (Transcription.app != COMMAND_APP))
        rows = _apply_date_filter(query, start, end).all()
        event_period = func.strftime(fmt, LearnEventRow.created_at)
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
