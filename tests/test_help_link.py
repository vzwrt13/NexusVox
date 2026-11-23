"""The online guide opens from the tray menu and from the dashboard, and both use the one URL."""

import re
import sys

import pytest

from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.system.links import GUIDE_URL


def test_dashboard_help_link_points_to_the_guide(db, session_factory):
    app = _create_app(DashboardAPI(session_factory, Config(), db=db))
    app.config["TESTING"] = True
    html = app.test_client().get("/").get_data(as_text=True)
    match = re.search(r'<a [^>]*id="help-link"[^>]*>', html)
    assert match, "the sidebar has no help link"
    assert f'href="{GUIDE_URL}"' in match.group(0)
    assert 'target="_blank"' in match.group(0)
    assert "{{" not in html


def _tray_help_item(tray):
    menu = tray.SystemTray(on_quit=lambda: None)._build_menu()
    return next(item for item in menu.items if item.text == "Help")


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows-only")
def test_tray_menu_help_entry_opens_the_guide(monkeypatch):
    from nexusvox.system import tray

    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", lambda url: opened.append(url) or True)
    _tray_help_item(tray)(None)
    assert opened == [GUIDE_URL]


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows-only")
def test_tray_help_logs_when_no_browser_starts(monkeypatch, caplog):
    from nexusvox.system import tray

    monkeypatch.setattr(tray.webbrowser, "open", lambda url: False)
    _tray_help_item(tray)(None)
    assert "online guide" in caplog.text
