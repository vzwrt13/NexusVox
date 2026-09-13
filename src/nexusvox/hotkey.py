"""Global hotkey listener for voice input: hold (push-to-talk) or toggle mode."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from pynput import keyboard

from .config import HOTKEY_KEY_VKS, HotkeyConfig

# Win32 virtual-key codes of the modifiers, left and right variants included: the
# low-level hook reports the side-specific code, GetAsyncKeyState wants the generic one.
_MODIFIER_VKS: dict[str, tuple[int, ...]] = {
    "ctrl": (0x11, 0xA2, 0xA3),  # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
    "shift": (0x10, 0xA0, 0xA1),  # VK_SHIFT, VK_LSHIFT, VK_RSHIFT
    "alt": (0x12, 0xA4, 0xA5),  # VK_MENU, VK_LMENU, VK_RMENU
    "win": (0x5B, 0x5C),  # VK_LWIN, VK_RWIN
}
_VK_TO_MODIFIER: dict[int, str] = {vk: name for name, vks in _MODIFIER_VKS.items() for vk in vks}

# Window messages the WH_KEYBOARD_LL hook delivers (SYS* variants while Alt is down)
_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
_PRESS_MESSAGES = (_WM_KEYDOWN, _WM_SYSKEYDOWN)


class HotkeyListener:
    """Listens for a modifier combo, optionally completed by one ordinary key.

    Everything is decided inside the low-level keyboard hook (`_handle`), on Win32
    virtual-key codes, because that is the only place an event can still be swallowed:
    when the binding ends in a key such as `A`, the app under the cursor must never
    receive that `A`. The rule is simple: the key-down that completes the combo is
    swallowed, and from then on every event of that key (auto-repeats and the final
    key-up) is swallowed too, until the key is physically released. The modifiers are
    always passed through, exactly as before. So in hold mode the letter stays dead for
    the whole recording, and in toggle mode it types again the moment the user lets go.

    The binding and `config.mode` are re-read from the shared config on every event,
    so a change in the dashboard takes effect immediately:

    - "hold": activate on the complete press, deactivate when the first key goes up
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
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate

        self._binding: tuple[tuple[str, ...], str] | None = None
        self._modifiers: frozenset[str] = frozenset()
        self._key_vk: int | None = None
        self._pressed_modifiers: set[str] = set()
        self._combo_down = False  # complete combo currently pressed (edge detection)
        self._key_swallowed = False  # trigger key is held since a swallowed key-down
        self._sync_binding()

        self._active = False
        self._listener: keyboard.Listener | None = None

    @property
    def mode(self) -> str:
        return self._config.mode

    @property
    def active(self) -> bool:
        """True between the activate and deactivate callbacks (recording is intended)."""
        return self._active

    def _sync_binding(self) -> None:
        """Pick up a changed `[hotkey]` binding from the shared config."""
        binding = (tuple(self._config.modifiers), self._config.key)
        if binding == self._binding:
            return
        self._binding = binding
        self._modifiers = frozenset(self._config.modifiers)
        self._key_vk = HOTKEY_KEY_VKS.get(self._config.key) if self._config.key else None
        # Combo tracking restarts from "nothing pressed"; a running toggle recording
        # keeps its `_active` so the next complete press still stops it.
        self._pressed_modifiers = set()
        self._combo_down = False
        self._key_swallowed = False

    # -- core -----------------------------------------------------------------

    def _complete_press(self) -> None:
        self._combo_down = True
        if self._active:
            if self.mode == "toggle":
                self._active = False
                self._on_deactivate()
        else:
            self._active = True
            self._on_activate()

    def _combo_broken(self) -> None:
        self._combo_down = False
        if self._active and self.mode == "hold":
            self._active = False
            self._on_deactivate()

    def _handle(self, vk: int, pressed: bool) -> bool:
        """Feed one key event. Returns True if the event must be swallowed."""
        self._sync_binding()

        if vk == self._key_vk:
            if pressed:
                if self._key_swallowed:
                    return True  # auto-repeat while held as part of the combo
                if self._combo_down or not self._modifiers <= self._pressed_modifiers:
                    return False  # plain typing: the modifiers are not (all) down
                self._key_swallowed = True
                self._complete_press()
                return True
            if self._combo_down:
                self._combo_broken()
            swallowed, self._key_swallowed = self._key_swallowed, False
            return swallowed

        modifier = _VK_TO_MODIFIER.get(vk)
        if modifier is None or modifier not in self._modifiers:
            return False
        if pressed:
            self._pressed_modifiers.add(modifier)
            # A modifier-only binding completes on its last modifier. With a key, the key
            # has to come last, otherwise "hold A, then press Ctrl" would start recording.
            if self._key_vk is None and not self._combo_down and self._modifiers <= self._pressed_modifiers:
                self._complete_press()
            return False
        self._pressed_modifiers.discard(modifier)
        if self._combo_down:
            self._combo_broken()
        return False

    def _win32_event_filter(self, msg: int, data) -> None:
        """pynput hook callback, runs synchronously inside WH_KEYBOARD_LL."""
        if self._handle(data.vkCode, msg in _PRESS_MESSAGES) and self._listener is not None:
            self._listener.suppress_event()

    # -- state queries --------------------------------------------------------

    def cancel(self) -> None:
        """Drop the recording intent without firing on_deactivate.

        The app calls this when a cycle aborts before its recording was stopped.
        In toggle mode nothing else would ever clear `_active`: the next press
        would count as the "stop" of a recording that no longer exists, and the
        mic guard would stay muted because `still_recording()` keeps saying yes.
        """
        self._active = False

    def modifiers_physically_down(self) -> bool:
        """True while every key of the binding is physically held, asked straight from Win32.

        Independent of the pynput hook, so it still answers correctly if a key-up event
        was lost (used by the mic guard watchdog to tell a long dictation from a stuck hold).
        """
        user32 = ctypes.windll.user32
        vks = [_MODIFIER_VKS[m][0] for m in self._modifiers]
        if self._key_vk is not None:
            vks.append(self._key_vk)
        return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)

    def still_recording(self) -> bool:
        """Whether the user still means to record: physical keys in hold mode, the
        toggle state in toggle mode (there the keys are up while recording)."""
        if self.mode == "toggle":
            return self._active
        return self.modifiers_physically_down()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Start listening for the hotkey. Runs the listener in a daemon thread."""
        self._listener = keyboard.Listener(win32_event_filter=self._win32_event_filter)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop the hotkey listener."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def describe_hotkey(config: HotkeyConfig) -> str:
    """Human-readable binding, e.g. "Ctrl+Shift+A" or "Ctrl+Shift+Alt"."""
    parts = [m.capitalize() for m in config.modifiers]
    if config.key:
        parts.append(config.key.replace("_", " ").title() if len(config.key) > 1 else config.key)
    return "+".join(parts)
