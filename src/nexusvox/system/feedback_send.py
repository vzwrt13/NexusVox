"""Sending a report from the dashboard's Feedback tab: one POST, only on the user's click.

The Worker behind `[support].feedback_form_url` takes plain text at `/api/app-feedback`: kind, message, the
app, Windows and hardware facts and, when the user ticks the box, the end of today's log. This module builds that
body (`build_report`), shows it as text (`render_preview`) and posts it (`send_report`). Nothing here runs by
itself: the dashboard calls it from a click on *Preview* or *Send*.

The log leaves the PC only after `report_log` has cut out everything `logsetup` fenced as dictated text, with
full-text logging on or off, and only from the first start mark on. A report goes only over https to
`APP_FEEDBACK_HOSTS`.
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
# The folder after `Users`, spaces included, up to the next separator: drive, UNC and /c/Users forms alike.
_USER_DIR = re.compile(r"(?i)([\\/]users[\\/]+)[^\\/\r\n'\"<>|:*?]+")
APP_FEEDBACK_HOSTS = ("nexusvox.app", "www.nexusvox.app")  # the only hosts a report and log are ever posted to


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
    """The Worker's text path on the web form's host, or "" when sending from the app is not available.

    A message and a log leave the PC only over https and only to a host in `APP_FEEDBACK_HOSTS`: a form URL
    that points to another site (a hosted form service, say) keeps its web form and gets no in-app Send."""
    try:
        parts = urlsplit(form_url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return ""
    if parts.scheme != "https" or host not in APP_FEEDBACK_HOSTS or port not in (None, 443) or parts.username:
        return ""
    return urlunsplit(("https", host, APP_FEEDBACK_PATH, "", ""))


def strip_dictated_text(log: str) -> str:
    """The log without anything fenced as dictated text, and without the Windows user name in paths."""
    log = _FENCED.sub(REMOVED_MARK, log)
    log = log.replace(TEXT_START, "").replace(TEXT_END, "")  # a stray fence is never sent either
    return _USER_DIR.sub(r"\1<user>", log)


def _mark_before(handle, end: int) -> bool:
    """Whether `LOG_FORMAT_MARK` appears in the file before byte offset `end`, read in chunks from the start."""
    needle = LOG_FORMAT_MARK.encode()
    handle.seek(0)
    carry = b""
    read = 0
    while read < end:
        chunk = handle.read(min(1024 * 1024, end - read))
        if not chunk:
            return False
        read += len(chunk)
        if needle in carry + chunk:
            return True
        carry = chunk[-len(needle) :]
    return False


def _drop_cut_fence(text: str) -> str:
    """A read that starts mid-file may start inside a fenced text: drop up to its closing fence."""
    end = text.find(TEXT_END)
    start = text.find(TEXT_START)
    if end >= 0 and (start < 0 or end < start):
        return text[end + 1 :]
    return text


_cache: tuple[tuple, str] | None = None


def report_log(directory: Path | None = None) -> str:
    """The last `LOG_LINES` lines (at most `LOG_MAX_CHARS` characters) of today's log, dictated text removed.

    Everything from the file's first start mark on was written by a version that fences dictated text, restarts
    included, so a report after a restart still holds the session that went wrong. Lines before that mark may
    not be fenced and are never sent. "" when there is no log or no such mark. The result is kept until the file
    changes, so a preview that refreshes while the user types does not read the log again."""
    global _cache
    path = (directory or log_dir()) / LOG_FILE_NAME
    try:
        info = path.stat()
        key = (str(path), info.st_size, info.st_mtime_ns)
        if _cache and _cache[0] == key:
            return _cache[1]
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            offset = max(0, size - _READ_LIMIT)
            handle.seek(offset)
            text = handle.read().decode("utf-8", errors="replace")
            if not (offset and _mark_before(handle, offset)):  # else the whole window follows a mark
                start = text.find(LOG_FORMAT_MARK)
                text = text[text.rfind("\n", 0, start) + 1 :] if start >= 0 else ""
    except OSError:
        return ""
    if offset:
        text = _drop_cut_fence(text)
    lines = strip_dictated_text(text).splitlines()[-LOG_LINES:]
    while len(lines) > 1 and sum(len(line) + 1 for line in lines) > LOG_MAX_CHARS:
        lines.pop(0)
    log = "\n".join(lines)[-LOG_MAX_CHARS:]
    _cache = (key, log)
    return log


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
    text = _encodable(message).strip() if isinstance(message, str) else ""
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
        log = _encodable(report_log(log_directory))
        if log:
            report["log"] = log
    return report


def _encodable(text: str) -> str:
    """`text` with a lone surrogate (half an emoji from the page) replaced, so it can be encoded and shown as sent."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


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
    body = json.dumps({key: _encodable(value) for key, value in report.items()}, ensure_ascii=False)
    try:
        response = httpx.post(url, content=body.encode("utf-8"), headers=headers, timeout=TIMEOUT_S)
    except httpx.InvalidURL as exc:
        raise FeedbackError("disabled", "The feedback address in config.toml is not valid.") from exc
    except httpx.HTTPError as exc:
        logger.info("Feedback not sent: %s", exc.__class__.__name__)
        raise FeedbackError(
            "offline", "No connection to the feedback server. Your text is kept: try again later or use the web form."
        ) from exc
    if response.status_code != 200:
        logger.info("Feedback refused: HTTP %d", response.status_code)
        raise _error_for(response)
