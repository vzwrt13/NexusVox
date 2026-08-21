"""System tray icon and menu."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw


def _create_icon_image(color: str = "green") -> Image.Image:
    """Create a simple colored circle icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=color)
    return img


class SystemTray:
    """System tray icon with right-click menu."""

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_toggle_language: Callable[[], None],
        get_language: Callable[[], str],
        on_open_dashboard: Callable[[], None] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_toggle_language = on_toggle_language
        self._get_language = get_language
        self._on_open_dashboard = on_open_dashboard
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _build_menu(self) -> pystray.Menu:
        items = [
            pystray.MenuItem(
                lambda _: f"Language: {self._get_language().upper()}",
                self._handle_toggle_language,
            ),
        ]
        if self._on_open_dashboard is not None:
            items.append(pystray.MenuItem("Dashboard", self._handle_open_dashboard))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._handle_quit))
        return pystray.Menu(*items)

    def _handle_open_dashboard(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._on_open_dashboard is not None:
            self._on_open_dashboard()

    def _handle_toggle_language(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._on_toggle_language()
        icon.update_menu()

    def _handle_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        icon.stop()
        self._on_quit()

    def set_active(self, active: bool) -> None:
        """Update tray icon to reflect recording state."""
        if self._icon is not None:
            self._icon.icon = _create_icon_image("red" if active else "green")

    def start(self) -> None:
        """Start the system tray icon in a background thread."""
        self._icon = pystray.Icon(
            name="NexusVox",
            icon=_create_icon_image("green"),
            title="NexusVox — Ready",
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the system tray icon."""
        if self._icon is not None:
            self._icon.stop()
