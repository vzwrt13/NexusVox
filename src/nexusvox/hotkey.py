"""Global hotkey listener for push-to-talk."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from pynput import keyboard

from .config import HotkeyConfig

# Map config strings to pynput key objects
_MODIFIER_MAP: dict[str, keyboard.Key] = {
    "ctrl": keyboard.Key.ctrl_l,
    "shift": keyboard.Key.shift_l,
    "alt": keyboard.Key.alt_l,
    "win": keyboard.Key.cmd,
}

# Virtual-key codes for GetAsyncKeyState, same order of meaning as _MODIFIER_MAP
_VK_MAP: dict[str, int] = {
    "ctrl": 0x11,  # VK_CONTROL
    "shift": 0x10,  # VK_SHIFT
    "alt": 0x12,  # VK_MENU
    "win": 0x5B,  # VK_LWIN
}


class HotkeyListener:
    """Listens for a modifier-only push-to-talk hotkey (hold to record, release to stop)."""

    def __init__(
        self,
        config: HotkeyConfig,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
    ) -> None:
        self._modifiers = {_MODIFIER_MAP[m] for m in config.modifiers}
        self._vks = [_VK_MAP[m] for m in config.modifiers]
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate

        self._pressed_modifiers: set[keyboard.Key] = set()
        self._active = False
        self._listener: keyboard.Listener | None = None

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in self._modifiers:
            self._pressed_modifiers.add(key)
            if not self._active and self._modifiers.issubset(self._pressed_modifiers):
                self._active = True
                self._on_activate()

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in self._modifiers:
            self._pressed_modifiers.discard(key)
            if self._active and not self._modifiers.issubset(self._pressed_modifiers):
                self._active = False
                self._on_deactivate()

    def modifiers_physically_down(self) -> bool:
        """True while every hotkey modifier is physically held, asked straight from Win32.

        Independent of the pynput hook, so it still answers correctly if a key-up event
        was lost (used by the mic guard watchdog to tell a long dictation from a stuck hold).
        """
        user32 = ctypes.windll.user32
        return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in self._vks)

    def start(self) -> None:
        """Start listening for the hotkey. Runs the listener in a daemon thread."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop the hotkey listener."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
