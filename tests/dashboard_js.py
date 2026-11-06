"""The dashboard's own scripts as index.html loads them, for the tests that read their source."""

from pathlib import Path

from nexusvox.dashboard.scripts import page_scripts

STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"


def script_names() -> list[str]:
    """The `dashboard-*.js` files of index.html, in load order (a bundled library is not one of them)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return [name for name in page_scripts(html) if name.startswith("dashboard-")]


def dashboard_source() -> str:
    """All scripts joined in load order, so a test can search or slice them like one file."""
    return "".join((STATIC / name).read_text(encoding="utf-8") for name in script_names())
