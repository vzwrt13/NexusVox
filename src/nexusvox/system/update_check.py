"""Is a newer release out? One GET to the GitHub API, only when the user clicks *Check for updates*.

Nothing is downloaded or installed from here: the answer is a message and a link to the release page. The request
carries no identifier of the user or the PC (no cookie, no account, no machine id); the User-Agent names the app
and its version, which GitHub requires some User-Agent for.
"""

from __future__ import annotations

import logging
import re
from importlib.metadata import PackageNotFoundError, version

import httpx

from nexusvox import __version__

logger = logging.getLogger(__name__)

REPO = "vzwrt13/NexusVox"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
TIMEOUT_S = 5.0

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


def installed_version() -> str:
    """The version of the installed package; the source constant when there is no package metadata (frozen build)."""
    try:
        return version("nexusvox")
    except PackageNotFoundError:
        return __version__


def parse_version(text: object) -> tuple[int, ...] | None:
    """`v1.2.3` / `1.2` as a tuple of numbers, ignoring a pre-release suffix; None when it is not a version."""
    match = _VERSION_RE.match(text.strip()) if isinstance(text, str) else None
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # 1.0 and 1.0.0 are the same release
        parts.pop()
    return tuple(parts)


def _release_page(url: object, tag: str) -> str:
    """The release page from the reply when it is on this repository's GitHub page, else a link built from the tag."""
    if isinstance(url, str) and url.startswith(f"https://github.com/{REPO}/"):
        return url
    return f"{RELEASES_URL}/tag/{tag}" if tag else RELEASES_URL


def check_for_update(current: str | None = None) -> dict:
    """`{"ok": True, "current", "latest", "newer", "url", "message"}`, or `{"error", "message"}` when it cannot tell."""
    current = current or installed_version()
    headers = {"User-Agent": f"NexusVox/{current}", "Accept": "application/vnd.github+json"}
    try:
        response = httpx.get(LATEST_URL, headers=headers, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.info("Update check failed: %s", exc.__class__.__name__)
        return {"error": "offline", "message": "Could not reach GitHub. Check your internet connection and try again."}
    if response.status_code == 404:  # GitHub answers 404 while the repository has no published release
        return {
            "ok": True,
            "current": current,
            "latest": None,
            "newer": False,
            "url": RELEASES_URL,
            "message": "No release has been published yet.",
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
    url = _release_page(body.get("html_url"), tag)
    shown = _VERSION_RE.match(tag.strip()).group(1)
    if latest > have:
        message = f"Version {shown} is available."
    else:
        message = "You have the newest version."
    return {"ok": True, "current": current, "latest": shown, "newer": latest > have, "url": url, "message": message}
