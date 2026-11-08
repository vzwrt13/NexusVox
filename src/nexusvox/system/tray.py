"""System tray icon and menu."""

from __future__ import annotations

import ctypes
import threading
import winreg
from collections.abc import Callable
from pathlib import Path

import pystray
from PIL import Image

# The tray drawings of the NexusVox logo kit (pixel-fitted for 16 to 48 px). ``tray-N`` is transparent and meant for a
# dark taskbar; ``tile-N`` sits on the dark #0d0d10 tile and stays readable on a light taskbar.
_ICON_DIR = Path(__file__).resolve().parent / "tray_icons"
_SIZES = (16, 20, 24, 32, 48)
_VIOLET = (108, 92, 231)
_RECORDING_COLOR = (255, 107, 107)  # #ff6b6b replaces the mint stroke while recording


def _small_icon_size() -> int:
    """The tray icon size of this display (16 px at 100 % scaling), as the nearest drawn size."""
    try:
        wanted = ctypes.windll.user32.GetSystemMetrics(49)  # SM_CXSMICON
    except (AttributeError, OSError):
        wanted = 32
    return min(_SIZES, key=lambda size: abs(size - wanted)) if wanted else 32


def _taskbar_is_light() -> bool:
    """True when Windows draws the taskbar light (the SystemUsesLightTheme setting)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False


def _recolor_mint(img: Image.Image) -> Image.Image:
    """Turn the mint stroke red: each pixel moves from violet toward red by how mint it is."""
    out = img.convert("RGBA")
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            mint = max(0.0, min(1.0, (g - r + 16) / 226))
            blended = tuple(round(v + (t - v) * mint) for v, t in zip(_VIOLET, _RECORDING_COLOR, strict=True))
            if b < 200 and g > 150:  # an opaque mint pixel
                blended = _RECORDING_COLOR
            pixels[x, y] = (*blended, a)
    return out


def _create_icon_image(active: bool = False, light_taskbar: bool = False, size: int | None = None) -> Image.Image:
    """The NexusVox mark from the logo kit: a violet and a mint stroke that form an N and a sound wave.

    The mint stroke turns red while recording. On a light taskbar the mark sits on its dark tile.
    """
    size = size or _small_icon_size()
    name = "tile" if light_taskbar else "tray"
    img = Image.open(_ICON_DIR / f"{name}-{size}.png").convert("RGBA")
    return _recolor_mint(img) if active else img


class SystemTray:
    """System tray icon: left-click opens the dashboard, right-click shows the menu."""

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_toggle_translate: Callable[[], None] | None = None,
        get_translate: Callable[[], bool] | None = None,
        on_open_dashboard: Callable[[], None] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_toggle_translate = on_toggle_translate
        self._get_translate = get_translate
        self._on_open_dashboard = on_open_dashboard
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None
        self._active = False

    def _build_menu(self) -> pystray.Menu:
        items: list[pystray.MenuItem] = []
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

    def _handle_toggle_translate(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._on_toggle_translate is not None:
            self._on_toggle_translate()
        icon.update_menu()

    def _handle_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        icon.stop()
        self._on_quit()

    def set_active(self, active: bool) -> None:
        """Update tray icon to reflect recording state."""
        self._active = active
        if self._icon is not None:
            self._icon.icon = _create_icon_image(active, _taskbar_is_light())

    def start(self) -> None:
        """Start the system tray icon in a background thread."""
        self._icon = pystray.Icon(
            name="NexusVox",
            icon=_create_icon_image(light_taskbar=_taskbar_is_light()),
            title="NexusVox — Ready",
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the system tray icon."""
        if self._icon is not None:
            self._icon.stop()
