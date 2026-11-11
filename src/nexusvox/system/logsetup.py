"""Logging to a rotating file next to the console output.

Dictated and transcribed text is wrapped in `Text` where it is logged. The console prints it in full; the
file shortens it (first characters plus the length) unless `[general].log_full_text` is on.

Either way the file fences what came from a `Text` between two private-use characters (`TEXT_START`,
`TEXT_END`), so a log report can cut it out again whether it is shortened or whole. Every start of the app, and
every rollover, writes `LOG_FORMAT_MARK`: a file part before the last mark may predate the fences and is never sent.
"""

from __future__ import annotations

import copy
import logging
import os
import sys
import time
from collections.abc import Callable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "nexusvox.log"
KEEP_DAYS = 7
SHORT_TEXT_CHARS = 20
TEXT_START = ""
TEXT_END = ""
LOG_FORMAT_MARK = "Log started: dictated text is fenced for log reports"


class Text(str):
    """Spoken or transcribed text passed as a log argument; the file log shortens it."""


class _Fenced(str):
    """What the file log shows for a `Text`, between the fences: the same shape for %s and %r."""

    _repr: str

    def __new__(cls, shown: str, as_repr: str) -> _Fenced:
        obj = super().__new__(cls, TEXT_START + shown + TEXT_END)
        obj._repr = TEXT_START + as_repr + TEXT_END
        return obj

    def __str__(self) -> str:
        return str.__str__(self)

    def __repr__(self) -> str:
        return self._repr


def shorten(text: str) -> str:
    """`first 20 characters... (N chars)`; short text keeps its start and gains the length too."""
    head = text[:SHORT_TEXT_CHARS] + ("..." if len(text) > SHORT_TEXT_CHARS else "")
    return f"'{head}' ({len(text)} chars)"


class ShortenTextFormatter(logging.Formatter):
    """Formats a copy of the record with every `Text` argument fenced, and shortened unless full text is wanted.

    A copy, because the record is shared with the console handler, which keeps the original arguments.
    """

    def __init__(self, fmt: str, full_text: Callable[[], bool]) -> None:
        super().__init__(fmt)
        self._full_text = full_text

    def format(self, record: logging.LogRecord) -> str:
        if not record.args:
            return super().format(record)
        full = self._full_text()
        fenced = copy.copy(record)
        if isinstance(record.args, dict):
            fenced.args = {k: _fence(v, full) for k, v in record.args.items()}
        else:
            fenced.args = tuple(_fence(v, full) for v in record.args)
        return super().format(fenced)


def _fence(value: object, full: bool) -> object:
    if not isinstance(value, Text):
        return value
    text = str.__str__(value).replace(TEXT_START, "").replace(TEXT_END, "")  # the fences stay unambiguous
    return _Fenced(text, repr(text)) if full else _Fenced(shorten(text), shorten(text))


def harden_console_streams() -> None:
    """Make the console never fail on a character it cannot encode.

    A legacy Windows console (cp1252, cp850) refuses some characters of a log line (a path with an accent, a
    transcript); the logging handler then prints a bare `--- Logging error ---` instead of the line. Unencodable
    characters become `?`. A windowed app has no stream (None): nothing to do.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


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
        else:
            self.write_format_mark()  # the new file starts with the mark too
        delete_old_logs(Path(self.baseFilename).parent)

    def write_format_mark(self) -> None:
        self.emit(
            logging.makeLogRecord(
                {"name": __name__, "msg": LOG_FORMAT_MARK, "levelno": logging.INFO, "levelname": "INFO"}
            )
        )


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
    handler.write_format_mark()
    return directory / LOG_FILE_NAME


def set_file_log_level(level: int) -> None:
    """Sets the level of the file handler `setup_file_logging` added (known only once the config is read)."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, _DailyFileHandler):
            handler.setLevel(level)
