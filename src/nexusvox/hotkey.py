"""Global hotkey listener for voice input: hold (push-to-talk) or toggle mode."""

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
    """Listens for a modifier-only hotkey.

    `config.mode` is read on every key event, so switching it in the dashboard takes
    effect immediately:

    - "hold": activate when all modifiers are down, deactivate when the first goes up
    - "toggle": each complete press flips between active and inactive; releasing
      the keys changes nothing
    """

    def __init__(
        self,
        config: HotkeyConfig,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
    ) -> None:
        self._config = config
        self._modifiers = {_MODIFIER_MAP[m] for m in config.modifiers}
        self._vks = [_VK_MAP[m] for m in config.modifiers]
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate

        self._pressed_modifiers: set[keyboard.Key] = set()
        self._combo_down = False  # all modifiers currently pressed (edge detection)
        self._active = False
        self._listener: keyboard.Listener | None = None

    @property
    def mode(self) -> str:
        return self._config.mode

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key not in self._modifiers:
            return
        self._pressed_modifiers.add(key)
        if self._combo_down or not self._modifiers.issubset(self._pressed_modifiers):
            return
        self._combo_down = True
        if self._active:
            if self.mode == "toggle":
                self._active = False
                self._on_deactivate()
        else:
            self._active = True
            self._on_activate()

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key not in self._modifiers:
            return
        self._pressed_modifiers.discard(key)
        if self._modifiers.issubset(self._pressed_modifiers):
            return
        self._combo_down = False
        if self._active and self.mode == "hold":
            self._active = False
            self._on_deactivate()

    @property
    def active(self) -> bool:
        """True between the activate and deactivate callbacks (recording is intended)."""
        return self._active

    def modifiers_physically_down(self) -> bool:
        """True while every hotkey modifier is physically held, asked straight from Win32.

        Independent of the pynput hook, so it still answers correctly if a key-up event
        was lost (used by the mic guard watchdog to tell a long dictation from a stuck hold).
        """
        user32 = ctypes.windll.user32
        return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in self._vks)

    def still_recording(self) -> bool:
        """Whether the user still means to record: physical keys in hold mode, the
        toggle state in toggle mode (there the keys are up while recording)."""
        if self.mode == "toggle":
            return self._active
        return self.modifiers_physically_down()

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
