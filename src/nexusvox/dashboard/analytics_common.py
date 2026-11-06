"""Dashboard analytics: what the topic modules share."""

from __future__ import annotations

from ..models import Transcription


def _apply_date_filter(query, start: str | None, end: str | None, model=Transcription, *, polish: bool = False):
    """Filter a query by optional ISO date bounds (inclusive).

    Rows of polish attempts (`polish_status` set) are no dictations - no audio, no model, the label as
    text - so they stay out of every dictation statistic unless the query is for polish (`polish=True`).
    """
    if model is Transcription and not polish:
        query = query.filter(Transcription.polish_status.is_(None))
    if start:
        query = query.filter(model.created_at >= start)
    if end:
        query = query.filter(model.created_at < end + "T23:59:59")
    return query
