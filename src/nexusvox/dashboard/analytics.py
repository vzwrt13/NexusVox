"""SQLAlchemy query functions for dashboard analytics."""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..models import FileTranscription, Transcription

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
    }


_EMPTY_CLEANUP = {
    "cleanup_applied": 0,
    "cleanup_unchanged": 0,
    "cleanup_rejected": 0,
    "cleanup_timeouts": 0,
    "cleanup_failed": 0,
    "avg_cleanup_ms": None,
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
