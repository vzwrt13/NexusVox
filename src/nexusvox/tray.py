"""System tray icon and menu."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

# Brand colors from product/brand/ColorPalette (Tech Noir).md.
_READY_COLOR = (0, 229, 255, 255)  # Electric Cyan
_RECORDING_COLOR = (255, 59, 92, 255)  # Alert red while the microphone is open
_BACKGROUND_COLOR = (10, 14, 23, 255)  # Midnight Void


def _create_icon_image(active: bool = False) -> Image.Image:
    """Draw the NexusVox mark: a rounded dark tile with a five-bar waveform.

    Drawn at 4x and downsampled so the bars stay crisp at the 16 px the
    Windows tray uses. The waveform turns red while recording.
    """
    scale = 4
    size = 64 * scale
    color = _RECORDING_COLOR if active else _READY_COLOR
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=_BACKGROUND_COLOR)

    # Five vertical bars, heights as fractions of the tile, mirrored around the centre.
    heights = (0.28, 0.52, 0.76, 0.52, 0.28)
    bar_w = size * 0.12
    gap = size * 0.04
    total_w = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (size - total_w) / 2
    for h in heights:
        bar_h = size * h
        y0 = (size - bar_h) / 2
        draw.rounded_rectangle([x, y0, x + bar_w, y0 + bar_h], radius=bar_w / 2, fill=color)
        x += bar_w + gap

    return img.resize((64, 64), Image.LANCZOS)


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
                    lambda _: f"Translate to English: {'ON' if self._get_translate() else 'OFF'}",
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
