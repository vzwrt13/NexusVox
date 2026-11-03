"""The "Send feedback" link: the configured form URL plus three prefilled facts.

NexusVox makes no network call for this. The dashboard only hands the browser a URL; the
form (and where its answers go) is not part of the app. Only the app version, the Windows
version and the hardware tier are added, never a transcript, audio or log content.
"""

from __future__ import annotations

import platform
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nexusvox import __version__
from nexusvox.config import cuda_available


def hardware_tier() -> str:
    return "NVIDIA GPU" if cuda_available() else "CPU-only"


def windows_version() -> str:
    """E.g. `Windows 11 (10.0.26200)`; falls back to the platform name off Windows."""
    release, version, _csd, _ptype = platform.win32_ver()
    if release:
        return f"Windows {release} ({version})"
    return platform.system() or "unknown"


def build_feedback_url(
    form_url: str,
    *,
    app_version: str | None = None,
    windows: str | None = None,
    hardware: str | None = None,
) -> str:
    """`form_url` with `app_version`, `windows_version` and `hardware` query parameters, or ""
    when no form URL is configured (the button is then hidden). Parameters already on the
    URL are kept; a non-http(s) URL counts as unset, so the button never opens another scheme."""
    form_url = form_url.strip()
    parts = urlsplit(form_url)
    if not form_url or parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [
        ("app_version", app_version if app_version is not None else __version__),
        ("windows_version", windows if windows is not None else windows_version()),
        ("hardware", hardware if hardware is not None else hardware_tier()),
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))
