"""System tray icon and menu."""

from __future__ import annotations

import ctypes
import functools
import threading
from collections.abc import Callable
from pathlib import Path

import pystray
from PIL import Image

# The tray drawings of the NexusVox logo kit (pixel-fitted for 16 to 48 px). ``tray-N`` is transparent and meant for a
# dark taskbar; ``tile-N`` sits on the dark #0d0d10 tile and stays readable on a light taskbar.
_ICON_DIR = Path(__file__).resolve().parent / "tray_icons"
_SIZES = (16, 20, 24, 32, 48)

# A linear colour map that keeps violet (#6c5ce7) and the dark tile (#0d0d10) and sends mint (#00d2a0) to red
# (#ff6b6b). It is linear, so the anti-aliased edges between the colours stay correct. Rows: new R, G, B.
_MINT_TO_RED = (
    -0.2969, 1.08, 0.1762, 0,
    0.5238, 0.5637, -0.0712, 0,
    0.2696, -0.2245, 0.9634, 0,
)  # fmt: skip


def _small_icon_size() -> int:
    """The tray icon size of this display: the smallest drawn size that is not smaller than the wanted one.

    The process is not DPI aware, so Windows would always answer 16 px; the calling thread is made DPI aware for
    the query only. Scaling a larger drawing down looks better than stretching a smaller one up.
    """
    wanted = 16
    try:
        user32 = ctypes.windll.user32
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware v2
        try:
            wanted = round(16 * user32.GetDpiForSystem() / 96)
        finally:
            user32.SetThreadDpiAwarenessContext(old)
    except (AttributeError, OSError):
        pass
    return next((size for size in _SIZES if size >= wanted), _SIZES[-1])


def _taskbar_is_light() -> bool:
    """True when Windows draws the taskbar light (the SystemUsesLightTheme setting)."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except (ImportError, OSError):
        return False


def _recolor_mint(img: Image.Image) -> Image.Image:
    """Turn the mint stroke red and leave violet, the tile and the alpha channel as they are."""
    rgba = img.convert("RGBA")
    red = rgba.convert("RGB").convert("RGB", _MINT_TO_RED)
    red.putalpha(rgba.getchannel("A"))
    return red


@functools.cache
def _create_icon_image(active: bool = False, light_taskbar: bool = False, size: int | None = None) -> Image.Image:
    """The NexusVox mark from the logo kit: a violet and a mint stroke that form an N and a sound wave.

    The mint stroke turns red while recording. On a light taskbar the mark sits on its dark tile.
    The result is cached per argument set: the hotkey path asks for it on every press and release.
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
        self._light_taskbar = False

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
        if self._icon is not None:
            self._icon.icon = _create_icon_image(active, self._light_taskbar)

    def start(self) -> None:
        """Start the system tray icon in a background thread."""
        # Read once: the variant follows the taskbar theme at the start of the app.
        self._light_taskbar = _taskbar_is_light()
        self._icon = pystray.Icon(
            name="NexusVox",
            icon=_create_icon_image(light_taskbar=self._light_taskbar),
            title="NexusVox — Ready",
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the system tray icon."""
        if self._icon is not None:
            self._icon.stop()
