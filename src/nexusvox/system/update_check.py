"""Is a newer release out? One GET to the GitHub API, only when the user clicks *Check for updates*.

Nothing is downloaded or installed from here: the answer is a message and a link to the release page. The request
carries no identifier of the user or the PC (no cookie, no account, no machine id); the User-Agent names the app
and its version, which GitHub requires some User-Agent for.
"""

from __future__ import annotations

import logging
import re
import threading

import httpx

from nexusvox import __version__

logger = logging.getLogger(__name__)

REPO = "vzwrt13/NexusVox"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"


def is_release_url(url: object) -> bool:
    """True only for the project's own GitHub releases pages; nothing else may be opened from the dashboard."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    if url == RELEASES_URL:
        return True
    return url.startswith(RELEASES_URL) and url[len(RELEASES_URL)] in "/?#" and not url.startswith(RELEASES_URL + "/..")


# httpx applies this to each phase (connect, read, ...) and not to the DNS lookup, so `_get` also caps the whole call.
TIMEOUT_S = 5.0
DEADLINE_S = 8.0

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-.]?(dev|a|alpha|b|beta|c|rc|pre|preview)\.?(\d*))?", re.IGNORECASE)
# Pre-release kinds in order, all before the final release (rank 9).
_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}
_FINAL_RANK = 9


def installed_version() -> str:
    """The version of the code that is running: the same constant the feedback report names."""
    return __version__


def parse_version(text: object) -> tuple | None:
    """A sortable key for `v1.2.3` or `1.0.0rc1`; a pre-release sorts before its final. None if unreadable."""
    match = _VERSION_RE.match(text.strip()) if isinstance(text, str) else None
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # 1.0 and 1.0.0 are the same release
        parts.pop()
    kind = match.group(2)
    if kind is None:
        return tuple(parts), _FINAL_RANK, 0
    return tuple(parts), _PRE_RANK[kind.lower()], int(match.group(3) or 0)


def _release_page(url: object, tag: str) -> str:
    """The release page from the reply when it is on this repository's GitHub page, else a link built from the tag."""
    if isinstance(url, str) and url.startswith(f"https://github.com/{REPO}/"):
        return url
    return f"{RELEASES_URL}/tag/{tag}" if tag else RELEASES_URL


def _get(headers: dict[str, str]) -> httpx.Response:
    """One GET that gives up after `DEADLINE_S` in total (a slow DNS lookup included). Raises `httpx.HTTPError`."""
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["response"] = httpx.get(LATEST_URL, headers=headers, timeout=TIMEOUT_S, follow_redirects=True)
        except Exception as exc:  # handed to the caller's thread below
            box["error"] = exc

    worker = threading.Thread(target=run, name="update-check", daemon=True)
    worker.start()
    worker.join(DEADLINE_S)
    if "response" in box:
        return box["response"]  # type: ignore[return-value]
    error = box.get("error")
    if isinstance(error, httpx.HTTPError):
        raise error
    if error is not None:
        raise httpx.TransportError(error.__class__.__name__)
    raise httpx.TimeoutException("update check took too long")


def check_for_update(current: str | None = None) -> dict:
    """`{"ok": True, "current", "latest", "newer", "url", "message"}`, or `{"error", "message"}` when it cannot tell."""
    current = current or installed_version()
    headers = {"User-Agent": f"NexusVox/{current}", "Accept": "application/vnd.github+json"}
    try:
        response = _get(headers)
    except httpx.HTTPError as exc:
        logger.info("Update check failed: %s", exc.__class__.__name__)
        return {"error": "offline", "message": "Could not reach GitHub. Check your internet connection and try again."}
    if response.status_code == 404:
        # GitHub also answers 404 for a repository it cannot show, so this is "nothing to compare", never "up to date".
        return {
            "ok": True,
            "current": current,
            "latest": None,
            "newer": False,
            "url": RELEASES_URL,
            "message": "GitHub shows no published release yet, so there is nothing to compare with.",
        }
    if response.status_code != 200:
        logger.info("Update check refused: HTTP %d", response.status_code)
        return {
            "error": "server",
            "message": f"GitHub could not answer (HTTP {response.status_code}). Try again later.",
        }
    try:
        body = response.json()
    except ValueError:
        body = None
    tag = body.get("tag_name") if isinstance(body, dict) else None
    latest, have = parse_version(tag), parse_version(current)
    if latest is None or have is None:
        return {"error": "unreadable", "message": "GitHub's answer could not be read. Try again later."}
    shown = tag.strip().lstrip("vV")
    newer = latest > have
    return {
        "ok": True,
        "current": current,
        "latest": shown,
        "newer": newer,
        "url": _release_page(body.get("html_url"), tag.strip()),
        "message": f"Version {shown} is available." if newer else "You have the newest version.",
    }
