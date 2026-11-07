"""Sending a report from the dashboard's Feedback tab: one POST, only on the user's click.

The Worker behind `[support].feedback_form_url` takes plain text at `/api/app-feedback`: kind, message, the
app, Windows and hardware facts and, when the user ticks the box, the end of today's log. This module builds that
body (`build_report`), shows it as text (`render_preview`) and posts it (`send_report`). Nothing here runs by
itself: the dashboard calls it from a click on *Preview* or *Send*.

The log leaves the PC only after `report_log` has cut out everything `logsetup` fenced as dictated text, with
full-text logging on or off, and only the part written since the app last started.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from nexusvox import __version__
from nexusvox.system.feedback_form import hardware_tier, windows_version
from nexusvox.system.logsetup import LOG_FILE_NAME, LOG_FORMAT_MARK, TEXT_END, TEXT_START, log_dir

logger = logging.getLogger(__name__)

KINDS = ("bug", "wish", "other")
MESSAGE_MIN_CHARS = 3
MESSAGE_MAX_CHARS = 5000
LOG_MAX_CHARS = 50_000
LOG_LINES = 300
APP_FEEDBACK_PATH = "/api/app-feedback"
REMOVED_MARK = "[dictated text removed]"
TIMEOUT_S = 15.0

_READ_LIMIT = 16 * 1024 * 1024  # of a day's log, only this much from the end is read
_FENCED = re.compile(f"{TEXT_START}.*?(?:{TEXT_END}|\\Z)", re.DOTALL)
_USER_DIR = re.compile(r"(?i)([a-z]:[\\/]+users[\\/]+)[^\\/\s'\"<>|:*?]+")


class FeedbackError(Exception):
    """A report that cannot be sent; `code` is machine-readable, `message` is for the user."""

    def __init__(self, code: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after

    def as_dict(self) -> dict:
        body: dict = {"error": self.code, "message": self.message}
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        return body


def app_feedback_url(form_url: str) -> str:
    """The Worker's text path on the host of the web form, or "" when no (usable) form URL is set."""
    parts = urlsplit(form_url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, APP_FEEDBACK_PATH, "", ""))


def strip_dictated_text(log: str) -> str:
    """The log without anything fenced as dictated text, and without the Windows user name in paths."""
    log = _FENCED.sub(REMOVED_MARK, log)
    log = log.replace(TEXT_START, "").replace(TEXT_END, "")  # a stray fence is never sent either
    return _USER_DIR.sub(r"\1<user>", log)


def report_log(directory: Path | None = None) -> str:
    """The last `LOG_LINES` lines (at most `LOG_MAX_CHARS` characters) of today's log, dictated text removed.

    Only the part after the app's last start mark counts, because earlier lines may come from a version that did
    not fence dictated text. "" when there is no log or no such mark."""
    path = (directory or log_dir()) / LOG_FILE_NAME
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - _READ_LIMIT))
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    start = text.rfind(LOG_FORMAT_MARK)
    if start < 0:
        return ""
    text = text[text.rfind("\n", 0, start) + 1 :]
    lines = strip_dictated_text(text).splitlines()[-LOG_LINES:]
    while len(lines) > 1 and sum(len(line) + 1 for line in lines) > LOG_MAX_CHARS:
        lines.pop(0)
    return "\n".join(lines)[-LOG_MAX_CHARS:]


def build_report(
    kind: object,
    message: object,
    include_log: bool,
    *,
    device: str = "auto",
    log_directory: Path | None = None,
) -> dict[str, str]:
    """The JSON body for `/api/app-feedback`; raises `FeedbackError` for a kind or message the Worker would refuse."""
    if kind not in KINDS:
        raise FeedbackError("invalid", "Choose what the report is about.")
    text = message.strip() if isinstance(message, str) else ""
    if len(text) < MESSAGE_MIN_CHARS:
        raise FeedbackError("invalid", f"Write at least {MESSAGE_MIN_CHARS} characters.")
    if len(text) > MESSAGE_MAX_CHARS:
        raise FeedbackError("too_large", f"The message is too long: {MESSAGE_MAX_CHARS} characters at most.")
    report = {
        "kind": str(kind),
        "message": text,
        "app_version": __version__,
        "windows_version": windows_version(),
        "hardware": hardware_tier(device),
    }
    if include_log:
        log = report_log(log_directory)
        if log:
            report["log"] = log
    return report


def render_preview(url: str, report: Mapping[str, str]) -> str:
    """The report as the user reads it before sending: every field that goes out, nothing else."""
    log = report.get("log")
    parts = [
        f"To: {url}",
        f"Kind: {report['kind']}",
        f"NexusVox: {report['app_version']}",
        f"Windows: {report['windows_version']}",
        f"Hardware: {report['hardware']}",
        "",
        "Message:",
        report["message"],
        "",
    ]
    if log is None:
        parts.append("Log: not included")
    else:
        parts += [f"Log ({len(log.splitlines())} lines, dictated text removed):", log]
    return "\n".join(parts)


def _retry_after(response: httpx.Response) -> int | None:
    try:
        return max(1, int(response.headers.get("Retry-After", "")))
    except ValueError:
        return None


def _error_for(response: httpx.Response) -> FeedbackError:
    """What the Worker's refusal means for the user (the table is in the site repository's README)."""
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("error") if isinstance(body, dict) else None
    retry_after = _retry_after(response)
    if status == 429 and code == "daily_cap":
        return FeedbackError(
            "daily_cap",
            "Today's limit for reports sent from the app is reached. Try again tomorrow or use the web form.",
        )
    if status == 429:
        wait = f" in {retry_after} seconds" if retry_after else " in a minute"
        return FeedbackError("rate", f"Too many reports right now. Try again{wait}.", retry_after)
    if status == 413:
        return FeedbackError("too_large", "The report is too large for the server. Shorten the message.")
    if status == 400:
        return FeedbackError("invalid", "The server did not accept the message. Check it and try again.")
    return FeedbackError(
        "server", f"The feedback server could not take the report (HTTP {status}). Try again later or use the web form."
    )


def send_report(url: str, report: Mapping[str, str]) -> None:
    """Posts the report as JSON. Raises `FeedbackError`: `offline` when the server cannot be reached."""
    headers = {"User-Agent": f"NexusVox/{__version__}", "Content-Type": "application/json; charset=utf-8"}
    try:
        response = httpx.post(
            url,
            content=json.dumps(dict(report), ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        logger.info("Feedback not sent: %s", exc.__class__.__name__)
        raise FeedbackError(
            "offline", "No connection to the feedback server. Your text is kept: try again later or use the web form."
        ) from exc
    if response.status_code != 200:
        logger.info("Feedback refused: HTTP %d", response.status_code)
        raise _error_for(response)
