"""Dashboard analytics: the totals behind the overview cards."""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..models import COMMAND_APP, FileTranscription, Transcription
from ..os_commands import NEXUS_ACTIONS
from .analytics_common import _apply_date_filter

# Assumed average typing speed for "time saved" calculation.
_TYPING_WPM = 40


def get_overview(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Totals, WPM, duration, latency, and estimated time saved."""
    with session_factory() as session:
        query = session.query(Transcription.text, Transcription.duration_ms)
        rows = _apply_date_filter(query, start, end).all()

    command_rows = _command_row_stats(session_factory, start, end)
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
            **command_rows,
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
        **command_rows,
    }


_EMPTY_CLEANUP = {
    "cleanup_applied": 0,
    "cleanup_unchanged": 0,
    "cleanup_rejected": 0,
    "cleanup_timeouts": 0,
    "cleanup_failed": 0,
    "avg_cleanup_ms": None,
}


_POLISH_TAGS = ("polish", "extension")


def _split_label(label: str) -> tuple[str, str]:
    """A stored command label ``[tag] detail`` as (tag, detail); the tag is the label itself when it has none."""
    tag, _, detail = label.removeprefix("[").partition("] ")
    return tag, detail


def _is_polish_label(label: str) -> bool:
    """Whether a command label belongs to polish on selected text (``[polish ...]``, ``[extension ...]``)."""
    return _split_label(label)[0].split(" ")[0] in _POLISH_TAGS


def _command_kind(label: str) -> str:
    """The kind a stored label stands for: ``[nexus] snap left x`` -> "snap left", ``[assistant] x`` -> "assistant"."""
    tag, detail = _split_label(label)
    if tag == "nexus":
        words = detail.lower().split()
        # Longest action first, so a two-word action ("snap left") is not taken for a shorter one.
        for action in sorted((a["action"] for a in NEXUS_ACTIONS), key=len, reverse=True):
            if words[: len(action.split())] == action.split():
                return action
        return words[0] if words else tag
    return tag


def _command_row_stats(session_factory: sessionmaker, start: str | None, end: str | None, top: int = 3) -> dict:
    """What the command rows say: polish on selected text, and the voice commands that were saved.

    One query feeds both. Polish attempts (a `polish_status`, or a `[polish ...]` / `[extension ...]` label) are
    counted by their own card; every other `COMMAND_APP` row is a voice command. A command row is saved
    whether or not the command worked, so the count is of commands recognised, not of commands that succeeded.

    Polish rows from before `polish_status` existed carry only their label, and were saved only when the
    polish went through, so a `[polish] ...` or `[extension] ...` row without a status counts as applied or
    handled by an extension.
    """
    with session_factory() as session:
        query = session.query(Transcription.polish_status, Transcription.polish_ms, Transcription.text)
        query = _apply_date_filter(query, start, end, polish=True).filter(
            Transcription.polish_status.isnot(None) | (Transcription.app == COMMAND_APP)
        )
        rows = query.all()
    counts = {"applied": 0, "extension": 0, "timeout": 0, "failed": 0}
    times = []
    kinds: Counter[str] = Counter()
    for status, ms, text in rows:
        if status is None and not _is_polish_label(text):
            kinds[_command_kind(text)] += 1
            continue
        if status is None:
            tag = _split_label(text)[0]
            status = "extension" if tag == "extension" else "applied" if tag == "polish" else None
        if status in counts:
            counts[status] += 1
        if status == "applied" and ms is not None:  # a timeout or failure says nothing about the model's speed
            times.append(ms)
    # Ties go by kind, so the card does not change between refreshes.
    ranked = sorted(kinds.items(), key=lambda item: (-item[1], item[0]))[:top]
    return {
        "polish_applied": counts["applied"],
        "polish_extension": counts["extension"],
        "polish_timeouts": counts["timeout"],
        "polish_failed": counts["failed"],
        "avg_polish_ms": round(sum(times) / len(times)) if times else None,
        "command_count": sum(kinds.values()),
        "top_commands": [{"kind": kind, "count": count} for kind, count in ranked],
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
