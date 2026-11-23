"""The online guide opens from the tray menu and from the dashboard, and both use the one URL."""

import re
import sys
from pathlib import Path

import pytest

from nexusvox.system.links import GUIDE_URL

INDEX_HTML = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static" / "index.html"


def test_dashboard_help_link_points_to_the_guide():
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r'<a [^>]*id="help-link"[^>]*>', html)
    assert match, "the sidebar has no help link"
    assert f'href="{GUIDE_URL}"' in match.group(0)
    assert 'target="_blank"' in match.group(0)


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows-only")
def test_tray_menu_help_entry_opens_the_guide(monkeypatch):
    from nexusvox.system import tray

    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    menu = tray.SystemTray(on_quit=lambda: None)._build_menu()
    help_item = next(item for item in menu.items if item.text == "Help")
    help_item(None)
    assert opened == [GUIDE_URL]
