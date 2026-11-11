"""Dashboard analytics: when, how and where dictation is used."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from ..dictation.lang_detect import UNDETERMINED
from ..storage.models import Transcription
from .analytics_common import _apply_date_filter, parse_day, period_bucket


def get_transcriptions_over_time(
    session_factory: sessionmaker, period: str = "day", *, start: str | None = None, end: str | None = None
) -> dict:
    """Transcription counts grouped by day, week, or month."""
    with session_factory() as session:
        query = session.query(
            period_bucket(period, Transcription.created_at).label("period"),
            func.count().label("count"),
        )
        rows = _apply_date_filter(query, start, end).group_by("period").order_by("period").all()

    return {"labels": [r.period for r in rows], "values": [r.count for r in rows]}


def get_day_totals(session_factory: sessionmaker, start: str | None = None, end: str | None = None) -> dict[str, dict]:
    """Dictations, words and spoken milliseconds per local day (`YYYY-MM-DD`), days with a dictation only.

    Command rows are no dictation, so their label text is not counted. One query, so a report that needs the
    days of a range, their words and their total reads the rows once.
    """
    local_day = func.strftime("%Y-%m-%d", Transcription.created_at, "localtime")
    with session_factory() as session:
        query = session.query(local_day, Transcription.text, Transcription.duration_ms)
        rows = _apply_date_filter(query, start, end).all()

    days: dict[str, dict] = {}
    for day, text, duration_ms in rows:
        total = days.setdefault(day, {"count": 0, "words": 0, "duration_ms": 0})
        total["count"] += 1
        total["words"] += len(text.split())
        total["duration_ms"] += duration_ms
    return days


# Longest list `get_words_per_day` builds; an earlier `start` is raised so the newest days stay.
MAX_WORDS_PER_DAY_SPAN = 3660


def day_labels(first: date, last: date) -> list[str]:
    """Every ISO day from `first` to `last`, both included."""
    return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]


def get_words_per_day(session_factory: sessionmaker, start: str | None = None, end: str | None = None) -> dict:
    """Dictated words per local day, every day from `start` to `end` listed (0 for a day without dictation).

    Without `start` the list begins on the first day with a dictation, without `end` it runs to today. A bound
    that is not a `YYYY-MM-DD` date counts as missing, for the list and for the filter alike.
    """
    first, last = parse_day(start), parse_day(end)
    shown_last = last or date.today()
    # The cap bounds the query too, not just the list: older rows are never read.
    floor = (
        shown_last - timedelta(days=MAX_WORDS_PER_DAY_SPAN - 1)
        if (shown_last - date.min).days >= MAX_WORDS_PER_DAY_SPAN
        else date.min
    )
    first = max(first, floor) if first else None
    days = get_day_totals(session_factory, (first or floor).isoformat(), last and last.isoformat())
    first = first or (date.fromisoformat(min(days)) if days else None)
    last = shown_last
    if first is None or first > last:
        return {"labels": [], "values": []}
    labels = day_labels(first, last)
    return {"labels": labels, "values": [days[label]["words"] if label in days else 0 for label in labels]}


def get_language_distribution(
    session_factory: sessionmaker, *, start: str | None = None, end: str | None = None
) -> dict:
    """Count of transcriptions by language."""
    with session_factory() as session:
        query = session.query(
            Transcription.language,
            func.count().label("count"),
        )
        query = query.filter(Transcription.language != UNDETERMINED)  # nothing to tell: not a language
        rows = _apply_date_filter(query, start, end).group_by(Transcription.language).all()

    return {"labels": [r.language.upper() for r in rows], "values": [r.count for r in rows]}


def get_top_words(
    session_factory: sessionmaker, n: int = 20, *, start: str | None = None, end: str | None = None
) -> dict:
    """Most frequent words across all dictations; the text of a command row is a label, not spoken words."""
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
    """Transcription count by local hour of day (0-23).

    `created_at` is naive UTC; SQLite's `localtime` converts each row with the offset of its own date.
    """
    with session_factory() as session:
        query = session.query(
            func.strftime("%H", Transcription.created_at, "localtime").label("hour"),
            func.count().label("count"),
        )
        rows = _apply_date_filter(query, start, end).group_by("hour").order_by("hour").all()

    # Fill in missing hours with 0.
    hour_map = {r.hour: r.count for r in rows}
    labels = [f"{h:02d}" for h in range(24)]
    values = [hour_map.get(f"{h:02d}", 0) for h in range(24)]

    return {"labels": labels, "values": values}


def get_activity_heatmap(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> dict:
    """Day-of-week (0=Sun) x hour-of-day grid of transcription counts, in local time."""
    with session_factory() as session:
        query = session.query(
            func.strftime("%w", Transcription.created_at, "localtime").label("dow"),
            func.strftime("%H", Transcription.created_at, "localtime").label("hour"),
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


def get_latency_over_time(
    session_factory: sessionmaker, period: str = "day", *, start: str | None = None, end: str | None = None
) -> dict:
    """Average transcribe and injection latency grouped by time period."""
    with session_factory() as session:
        query = session.query(
            period_bucket(period, Transcription.created_at).label("period"),
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


# Friendly names for common apps, keyed by lower-case process file name. Any other process shows under its file name.
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
# Apps shown by name in the per-app table; the less busy ones are folded into "Other" so the card stays short.
MAX_APP_ROWS = 10


def app_label(process: str | None) -> str:
    """The name shown for a stored process name: friendly when common, the file name without `.exe` otherwise,
    "Unknown" when unrecorded.

    Process names are stored lower-case (`App._paste`), so they are looked up as they are.
    """
    if not process:
        return UNKNOWN_APP
    if process in APP_NAMES:
        return APP_NAMES[process]
    stem = process.removesuffix(".exe")
    return stem or UNKNOWN_APP


def get_app_breakdown(session_factory: sessionmaker, *, start: str | None = None, end: str | None = None) -> list[dict]:
    """Dictations and words per target app, busiest first; "Other" and "Unknown" always come last.

    Process names that share a friendly name (`olk.exe` and `outlook.exe`) are one row. Beyond the `MAX_APP_ROWS`
    busiest apps the rest is summed into "Other". Rows that ran a command instead of typing text are left out.
    """
    with session_factory() as session:
        query = session.query(Transcription.app, Transcription.text)
        rows = _apply_date_filter(query, start, end).all()

    groups: dict[str, dict] = {}
    for r in rows:
        label = app_label(r.app)
        group = groups.setdefault(label, {"app": label, "count": 0, "words": 0})
        group["count"] += 1
        group["words"] += len(r.text.split())
    tail = {OTHER_APP: 1, UNKNOWN_APP: 2}
    ranked = sorted(groups.values(), key=lambda g: (tail.get(g["app"], 0), -g["count"], g["app"]))
    named = [g for g in ranked if g["app"] not in tail]
    if len(named) > MAX_APP_ROWS:
        other = groups.setdefault(OTHER_APP, {"app": OTHER_APP, "count": 0, "words": 0})
        for g in named[MAX_APP_ROWS:]:
            other["count"] += g["count"]
            other["words"] += g["words"]
        named = named[:MAX_APP_ROWS]
    rest = sorted((g for g in groups.values() if g["app"] in tail), key=lambda g: tail[g["app"]])
    return named + rest
