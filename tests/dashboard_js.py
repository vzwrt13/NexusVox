"""The dashboard's own scripts as index.html loads them, for the tests that read their source."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"


def script_names() -> list[str]:
    """The `dashboard-*.js` files of index.html, in load order (chart.min.js is a bundled library)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return [name for name in re.findall(r'<script src="([^"]+)"></script>', html) if name != "chart.min.js"]


def dashboard_source() -> str:
    """All scripts joined in load order, so a test can search or slice them like one file."""
    return "".join((STATIC / name).read_text(encoding="utf-8") for name in script_names())
