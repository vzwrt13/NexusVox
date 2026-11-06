"""Dashboard analytics: what the topic modules share."""

from __future__ import annotations

from sqlalchemy import func

from ..storage.models import Transcription


def _apply_date_filter(query, start: str | None, end: str | None, model=Transcription, *, polish: bool = False):
    """Filter a query by optional ISO date bounds (inclusive), read as the user's local days.

    `created_at` is naive UTC; SQLite's `localtime` converts each row with the offset of its own date, so a
    dictation just after local midnight belongs to the new day.

    Rows of polish attempts (`polish_status` set) are no dictations - no audio, no model, the label as
    text - so they stay out of every dictation statistic unless the query is for polish (`polish=True`).
    """
    if model is Transcription and not polish:
        query = query.filter(Transcription.polish_status.is_(None))
    if start or end:
        local_day = func.strftime("%Y-%m-%d", model.created_at, "localtime")
        if start:
            query = query.filter(local_day >= start)
        if end:
            query = query.filter(local_day <= end)
    return query
