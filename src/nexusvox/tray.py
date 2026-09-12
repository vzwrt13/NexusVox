"""System tray icon and menu."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

# Logo colors, matching the dashboard's --accent / --green / --red tokens.
_RING_COLOR = "#6c5ce7"
_IDLE_COLOR = "#00d2a0"
_RECORDING_COLOR = "#ff6b6b"


def _create_icon_image(active: bool = False) -> Image.Image:
    """Render the NexusVox logo: an open ring around a status dot.

    Mirrors ``dashboard/static/logo.svg`` (64px grid: ring r=22, stroke 7, ~78° gap, dot r=8).
    The dot is green while idle and red while recording.
    """
    size = 64
    scale = 4  # draw oversized, then downsample for anti-aliased edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = s / 2
    ring_r = 22 * scale
    stroke = 7 * scale
    dot_r = 8 * scale
    # Arc angles follow the SVG: 0° at 3 o'clock, clockwise; gap of ~78° starting at 211°.
    start, end = -70, 211

    box = [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r]
    draw.arc(box, start=start, end=end, fill=_RING_COLOR, width=stroke)
    for angle in (start, end):  # round caps
        x = cx + ring_r * math.cos(math.radians(angle))
        y = cy + ring_r * math.sin(math.radians(angle))
        draw.ellipse([x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2], fill=_RING_COLOR)

    dot = _RECORDING_COLOR if active else _IDLE_COLOR
    draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=dot)

    return img.resize((size, size), Image.LANCZOS)


class SystemTray:
    """System tray icon: left-click opens the dashboard, right-click shows the menu."""

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_toggle_language: Callable[[], None],
        get_language: Callable[[], str],
        on_toggle_translate: Callable[[], None] | None = None,
        get_translate: Callable[[], bool] | None = None,
        on_open_dashboard: Callable[[], None] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_toggle_language = on_toggle_language
        self._get_language = get_language
        self._on_toggle_translate = on_toggle_translate
        self._get_translate = get_translate
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
        if self._on_toggle_translate is not None and self._get_translate is not None:
            items.append(
                pystray.MenuItem(
                    lambda _: f"Translate to English (local server): {'ON' if self._get_translate() else 'OFF'}",
                    self._handle_toggle_translate,
                )
            )
        if self._on_open_dashboard is not None:
            # ``default=True`` makes a left-click on the icon open the dashboard.
            items.append(pystray.MenuItem("Dashboard", self._handle_open_dashboard, default=True))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._handle_quit))
        return pystray.Menu(*items)

    def _handle_open_dashboard(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._on_open_dashboard is not None:
            self._on_open_dashboard()

    def _handle_toggle_language(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._on_toggle_language()
        icon.update_menu()

    def _handle_toggle_translate(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._on_toggle_translate is not None:
            self._on_toggle_translate()
        icon.update_menu()

    def _handle_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        icon.stop()
        self._on_quit()

    def set_active(self, active: bool) -> None:
        """Update tray icon to reflect recording state."""
        if self._icon is not None:
            self._icon.icon = _create_icon_image(active)

    def start(self) -> None:
        """Start the system tray icon in a background thread."""
        self._icon = pystray.Icon(
            name="NexusVox",
            icon=_create_icon_image(),
            title="NexusVox — Ready",
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the system tray icon."""
        if self._icon is not None:
            self._icon.stop()
