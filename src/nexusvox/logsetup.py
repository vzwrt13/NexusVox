"""Logging to a rotating file next to the console output.

Dictated and transcribed text is wrapped in `Text` where it is logged. The console prints it in full; the
file shortens it (first characters plus the length) unless `[general].log_full_text` is on.
"""

from __future__ import annotations

import copy
import logging
import os
import time
from collections.abc import Callable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "nexusvox.log"
KEEP_DAYS = 7
SHORT_TEXT_CHARS = 20


class Text(str):
    """Spoken or transcribed text passed as a log argument; the file log shortens it."""


class _Shortened(str):
    """What the file log shows for a `Text`: the same for %s and %r."""

    def __str__(self) -> str:
        return str.__str__(self)

    def __repr__(self) -> str:
        return str.__str__(self)


def shorten(text: str) -> str:
    """`first 20 characters... (N chars)`; short text keeps its start and gains the length too."""
    head = text[:SHORT_TEXT_CHARS] + ("..." if len(text) > SHORT_TEXT_CHARS else "")
    return f"'{head}' ({len(text)} chars)"


class ShortenTextFormatter(logging.Formatter):
    """Formats a copy of the record with every `Text` argument shortened, unless full text is wanted.

    A copy, because the record is shared with the console handler, which keeps the full text.
    """

    def __init__(self, fmt: str, full_text: Callable[[], bool]) -> None:
        super().__init__(fmt)
        self._full_text = full_text

    def format(self, record: logging.LogRecord) -> str:
        if self._full_text() or not record.args:
            return super().format(record)
        shortened = copy.copy(record)
        if isinstance(record.args, dict):
            shortened.args = {k: _short(v) for k, v in record.args.items()}
        else:
            shortened.args = tuple(_short(v) for v in record.args)
        return super().format(shortened)


def _short(value: object) -> object:
    return _Shortened(shorten(value)) if isinstance(value, Text) else value


def log_dir() -> Path:
    """The NexusVox/logs folder under %LOCALAPPDATA%, or under the home folder when the variable is unset."""
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "NexusVox" / "logs"


def delete_old_logs(directory: Path, keep_days: int = KEEP_DAYS, now: float | None = None) -> int:
    """Removes log files not written for *keep_days* days; returns how many went."""
    cutoff = (time.time() if now is None else now) - keep_days * 86400
    removed = 0
    for path in directory.glob(f"{LOG_FILE_NAME}*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue  # in use or already gone: the next start tries again
    return removed


class _DailyFileHandler(TimedRotatingFileHandler):
    """Rotates at midnight and keeps `KEEP_DAYS` days: retention is `delete_old_logs`, one definition.

    A rollover that fails (on Windows a second NexusVox process holds the file, so the rename is refused) is
    skipped until the next midnight; the stock handler would retry on every record and lose the log.
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            self.rolloverAt = self.computeRollover(int(time.time()))
        delete_old_logs(Path(self.baseFilename).parent)


def setup_file_logging(
    full_text: Callable[[], bool], directory: Path | None = None, level: int = logging.INFO
) -> Path | None:
    """Adds a daily rotating file handler to the root logger; returns the log file, None when it cannot be opened."""
    directory = directory or log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        delete_old_logs(directory)
        handler = _DailyFileHandler(directory / LOG_FILE_NAME, when="midnight", encoding="utf-8", delay=True)
    except OSError:
        return None  # a read-only profile must not stop the app from starting
    handler.setLevel(level)
    handler.setFormatter(ShortenTextFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", full_text))
    logging.getLogger().addHandler(handler)
    return directory / LOG_FILE_NAME
