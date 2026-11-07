"""The "Send feedback" link: the configured form URL plus three prefilled facts.

The app makes no network call for this link. The dashboard only hands the browser a URL; the
form (and where its answers go) is not part of the app. Only the app version, the Windows
version and the hardware tier are added, never a transcript, audio or log content. Sending a
report from the app itself, with an optional log, is `feedback_send.py`.
"""

from __future__ import annotations

import functools
import platform
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nexusvox import __version__
from nexusvox.config import cuda_available

_WINDOWS_11_BUILD = 22000


@functools.cache
def _gpu_present() -> bool:
    return cuda_available()


def hardware_tier(device: str = "auto") -> str:
    """ "NVIDIA GPU" when a CUDA GPU is usable and `[inference].device` does not force the CPU."""
    return "NVIDIA GPU" if device != "cpu" and _gpu_present() else "CPU-only"


@functools.cache
def windows_version() -> str:
    """E.g. `Windows 11 (10.0.26200)`; the platform name off Windows. Python 3.11 reports
    Windows 11 as release "10", so the build number decides."""
    getter = getattr(sys, "getwindowsversion", None)
    if getter is None:
        return platform.system() or "unknown"
    info = getter()
    release = "11" if info.build >= _WINDOWS_11_BUILD else str(info.major)
    return f"Windows {release} ({info.major}.{info.minor}.{info.build})"


def build_feedback_url(
    form_url: str,
    *,
    device: str = "auto",
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
        ("hardware", hardware if hardware is not None else hardware_tier(device)),
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))
