"""SQLAlchemy query functions for dashboard analytics."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..models import Transcription

# Assumed average typing speed for "time saved" calculation.
_TYPING_WPM = 40


def _apply_date_filter(query, start: str | None, end: str | None):
    """Filter a query by optional ISO date bounds (inclusive)."""
    if start:
        query = query.filter(Transcription.created_at >= start)
    if end:
        query = query.filter(Transcription.created_at < end + "T23:59:59")
    return query


def get_overview(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Total transcriptions, avg duration, WPM, and estimated time saved."""
    with session_factory() as session:
        query = session.query(Transcription.text, Transcription.duration_ms)
        rows = _apply_date_filter(query, start, end).all()

    if not rows:
        return {
            "total_transcriptions": 0,
            "avg_duration_ms": 0,
            "avg_wpm": 0,
            "min_wpm": 0,
            "max_wpm": 0,
            "time_saved_minutes": 0,
        }

    total = len(rows)
    total_duration_ms = sum(r.duration_ms for r in rows)
    total_words = sum(len(r.text.split()) for r in rows)

    avg_duration_ms = total_duration_ms / total
    avg_wpm = (total_words / (total_duration_ms / 60_000)) if total_duration_ms > 0 else 0
    # Time it would take to type those words minus time spent recording.
    time_saved_min = (total_words / _TYPING_WPM) - (total_duration_ms / 60_000)

    # Per-transcription WPM for min/max.
    per_wpm = []
    for r in rows:
        if r.duration_ms > 0:
            per_wpm.append(len(r.text.split()) / (r.duration_ms / 60_000))

    # Average confidence (only over transcriptions that have one).
    with session_factory() as session:
        conf_query = session.query(func.avg(Transcription.confidence)).filter(Transcription.confidence.isnot(None))
        avg_conf = _apply_date_filter(conf_query, start, end).scalar()

    return {
        "total_transcriptions": total,
        "avg_duration_ms": round(avg_duration_ms),
        "avg_wpm": round(avg_wpm, 1),
        "min_wpm": round(min(per_wpm), 1) if per_wpm else 0,
        "max_wpm": round(max(per_wpm), 1) if per_wpm else 0,
        "time_saved_minutes": round(max(time_saved_min, 0), 1),
        "avg_confidence": round(avg_conf, 4) if avg_conf is not None else None,
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


def get_unreviewed_transcriptions(session_factory: sessionmaker, limit: int = 50) -> list[dict]:
    """Return unreviewed transcriptions that have audio, oldest first."""
    with session_factory() as session:
        rows = (
            session.query(Transcription)
            .filter(Transcription.reviewed == 0, Transcription.audio_path.isnot(None))
            .order_by(Transcription.created_at.asc())
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
                "flagged": r.flagged,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ]


def get_confidence_over_time(
    session_factory: sessionmaker, period: str = "day", *, start: str | None = None, end: str | None = None
) -> dict:
    """Average confidence grouped by time period."""
    fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}.get(period, "%Y-%m-%d")

    with session_factory() as session:
        query = session.query(
            func.strftime(fmt, Transcription.created_at).label("period"),
            func.avg(Transcription.confidence).label("avg_confidence"),
        ).filter(Transcription.confidence.isnot(None))

        rows = _apply_date_filter(query, start, end).group_by("period").order_by("period").all()

    return {
        "labels": [r.period for r in rows],
        "values": [round(r.avg_confidence, 4) for r in rows],
    }
