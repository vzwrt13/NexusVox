"""Global hotkey listener for push-to-talk."""

from __future__ import annotations

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


class HotkeyListener:
    """Listens for a modifier-only push-to-talk hotkey (hold to record, release to stop)."""

    def __init__(
        self,
        config: HotkeyConfig,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
    ) -> None:
        self._modifiers = {_MODIFIER_MAP[m] for m in config.modifiers}
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
