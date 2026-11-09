"""Global hotkey listener for voice input: hold (push-to-talk) or toggle mode."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable

from pynput import keyboard

from ..config import COMMAND_KEY_VKS, HOTKEY_KEY_VKS, HotkeyConfig, MeetingConfig, OsControlConfig

logger = logging.getLogger("nexusvox.hotkey")

# What a marker key pressed during a recording turns that recording into.
COMMAND = "command"
PASTE_LAST = "paste_last"
POLISH = "polish"

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

_VK_ESCAPE = 0x1B
# The neutral key `_mask_menu_keys` injects when the combo completes; it reaches the hook after `_active`
# is set and is not the user typing.
_VK_MENU_MASK = 0xE8
_LLKHF_INJECTED = 0x10  # flag of a low-level keyboard event that came from SendInput, not the keyboard

# Hold mode: a foreign key discards the recording only this long after it started. A longer shortcut that
# begins with the chord (Ctrl+Win+Alt+letter) joins it within a fraction of a second; a key struck minutes
# into a dictation is the user typing on, and must not throw the take away.
FOREIGN_KEY_WINDOW_S = 1.0


def _is_typing_key(vk: int) -> bool:
    """False for virtual keys that are not a key the user struck as part of a shortcut: mouse buttons, browser,
    volume, media and launch keys (macro pads and keyboards send them any time), VK_PACKET, the menu mask and
    the reserved/OEM-clear codes at the top of the range."""
    return not (vk < 0x07 or 0xA6 <= vk <= 0xB7 or vk in (0xE7, _VK_MENU_MASK) or vk >= 0xF6)


# Toggle with a modifier-only binding: how long after the stop chord a marker key may still
# turn the recording into polish / paste-last / command before the recording is finished.
STOP_GRACE_S = 0.3

# Modifiers whose lone release opens a menu (the app's for Alt, Start for Win)
_MENU_MODIFIERS = frozenset({"alt", "win"})


def _send_menu_mask(modifiers, sender: Callable[[], object] | None) -> None:
    """Send the menu mask key when the chord holds Alt or Win (see `HotkeyListener._mask_menu_keys`)."""
    if not set(modifiers) & _MENU_MODIFIERS or sender is None:
        return
    try:
        sender()
    except Exception:
        logger.exception("Could not send the menu mask key")


def _load_mask_sender() -> Callable[[], object] | None:
    try:
        # Imported here (injector binds Win32 at import) and outside the hook, which has a tight timeout.
        from .injector import send_menu_mask

        return send_menu_mask
    except Exception:
        logger.exception("Menu mask key unavailable; releasing Alt or Win last may open a menu")
        return None


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

    Marker keys turn a recording into something else when pressed during it with every
    binding modifier down: the os-control command key (`command_config`, when enabled)
    marks it a command recording, and `[hotkey].paste_key` marks it "paste the last text
    again", and `[hotkey].polish_key` marks it "rewrite the selection by what is said".
    The first marker pressed wins and stays readable (`mark`)
    until the next recording starts; the key itself is swallowed so it never types.
    `on_command` fires once when a recording becomes a command.

    In toggle mode a marker key also ends the recording ("hotkey + P" stops and polishes),
    unless it is pressed in the very hold that started it (then it only marks). With a
    modifier-only binding the stop chord waits `grace_s` for such a marker before it
    finishes the recording. Escape during a recording cancels it (`on_cancel`); only that
    Escape is swallowed.

    A recording is also dropped through `on_cancel` when another shortcut turns out to be meant: a key that
    is neither a binding modifier, the binding's key, a marker nor Escape, or an extra modifier, goes down
    while the start chord is still held (Hold: within `FOREIGN_KEY_WINDOW_S` of the start; Toggle: until
    the chord is released; Ctrl+Win+Alt+letter, Ctrl+Win+Left). That key is not swallowed.
    Injected events and non-typing keys (volume, media, VK_PACKET) never count,
    and a chord that completes while a modifier outside the binding is already physically down does not
    start a recording at all.
    """

    def __init__(
        self,
        config: HotkeyConfig,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
        command_config: OsControlConfig | None = None,
        on_command: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        grace_s: float = STOP_GRACE_S,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
        physical_modifiers: Callable[[], set[str]] | None = None,
    ) -> None:
        self._config = config
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate
        self._command_config = command_config
        self._on_command = on_command
        self._on_cancel = on_cancel
        self._grace_s = grace_s
        self._timer_factory = timer_factory
        self._lock = threading.RLock()  # the grace timer finishes a recording from its own thread
        self._stop_timer: threading.Timer | None = None  # toggle stop chord waiting for a marker key
        self._start_hold = False  # the modifiers that started this recording are still held
        self._physical_modifiers = physical_modifiers or _physical_modifiers
        self._stop_requested = 0.0  # monotonic time of the stop chord / marker (before any grace window)
        self._escape_swallowed = False
        self._active_since = 0.0  # monotonic time the current recording started (hold: foreign-key window)
        self._mark: str | None = None  # what a marker key turned this recording into
        self._marker_swallowed: set[int] = set()  # marker keys held since a swallowed key-down

        self._binding: tuple[tuple[str, ...], str] | None = None
        self._modifiers: frozenset[str] = frozenset()
        self._key_vk: int | None = None
        self._pressed_modifiers: set[str] = set()
        self._combo_down = False  # complete combo currently pressed (edge detection)
        self._key_swallowed = False  # trigger key is held since a swallowed key-down
        self._modifier_vks_down: set[int] = set()  # modifier keys down, to tell an auto-repeat from a press
        self._mask_sender: Callable[[], None] | None = None  # set by start(); tests leave it unset
        self._sync_binding()

        self._active = False
        self._listener: keyboard.Listener | None = None

    @property
    def mode(self) -> str:
        return self._config.mode

    @property
    def command(self) -> bool:
        """True when the current (or just finished) recording was marked as a command."""
        return self._mark == COMMAND

    @property
    def mark(self) -> str | None:
        """What a marker key turned the current (or just finished) recording into:
        `COMMAND`, `PASTE_LAST`, `POLISH`, or None for a plain dictation."""
        return self._mark

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
        self._modifier_vks_down = set()

    # -- core -----------------------------------------------------------------

    def _heal_modifiers(self, keep: str | None = None) -> None:
        """Forget tracked modifiers that are no longer physically down.

        A key-up the hook never saw (Ctrl+Alt+Del, the lock screen) leaves a modifier "held" in the tracked
        set. With a key in the binding the next plain press of that key would start a recording and swallow
        the letter; with a modifier-only binding the next lone press of the other modifier (Win, to open
        Start) would complete the chord. Asked before a press decides, like `MeetingHotkey` does.
        `keep` is the modifier being pressed right now: inside the hook its key state is not updated yet.
        """
        if not self._pressed_modifiers:
            return
        try:
            physical = self._physical_modifiers()
        except Exception:
            return  # no Win32 (a headless runner): nothing to compare with
        stale = self._pressed_modifiers - physical - {keep}
        if stale:
            logger.debug("Dropped modifiers without a key-up: %s", ", ".join(sorted(stale)))
            self._pressed_modifiers -= stale
            self._modifier_vks_down = {vk for vk in self._modifier_vks_down if _VK_TO_MODIFIER[vk] not in stale}

    def _extra_modifiers_down(self) -> bool:
        """A modifier outside the binding is already held as the chord completes: another shortcut.

        Only for a start; a Toggle stop chord is never refused."""
        if self._active:
            return False
        try:
            return bool(self._physical_modifiers() - self._modifiers)
        except Exception:
            return False  # no Win32 (a headless runner): nothing to compare with

    def _complete_press(self) -> None:
        self._combo_down = True
        self._mask_menu_keys()
        with self._lock:
            if self._active:
                if self.mode == "toggle" and self._stop_timer is None:
                    self._start_hold = False
                    self._stop_requested = time.monotonic()
                    if self._key_vk is None and self._mark is None and self._grace_s > 0:
                        # Modifier-only: the keys are already down, so "hotkey + marker" arrives after the chord.
                        self._stop_timer = self._timer_factory(self._grace_s, self._finish)
                        self._stop_timer.daemon = True
                        self._stop_timer.start()
                    else:
                        self._finish()
            else:
                self._active = True
                self._active_since = time.monotonic()
                self._start_hold = True
                self._mark = None
                self._on_activate()

    def _finish(self) -> None:
        """End the recording: the stop chord (after its grace window) or a marker key in toggle mode."""
        with self._lock:
            timer, self._stop_timer = self._stop_timer, None
            if timer is not None:
                timer.cancel()
            if self._active:
                self._active = False
                self._on_deactivate()

    def _cancel_recording(self) -> bool:
        """Escape, or a foreign key in hold mode: drop the recording without finishing it (`on_cancel`).
        False when it had already finished."""
        with self._lock:
            if not self._active:
                return False
            timer, self._stop_timer = self._stop_timer, None
            if timer is not None:
                timer.cancel()
            self._active = False
            if self._on_cancel is not None:
                self._on_cancel()
            return True

    def _discard_on_foreign_key(self) -> None:
        """A key that is neither part of the binding nor an assigned one joined the start chord.

        Ctrl+Win+Alt+letter or Ctrl+Win+Left is another shortcut that merely starts with the dictation
        chord, so the recording is dropped like an Escape cancel. The key is not swallowed. Hold: within
        `FOREIGN_KEY_WINDOW_S` of the start (the keys are held for the whole recording, so later typing
        is the user dictating on). Toggle: while the keys of the start chord are still held
        (`_start_hold`); once they are released, the recording runs on and typing never touches it.
        The checks run without the lock, like Escape's: this is called for every key typed anywhere.
        """
        if not self._active:
            return
        if self.mode == "hold":
            if time.monotonic() - self._active_since > FOREIGN_KEY_WINDOW_S:
                return
        elif not self._start_hold:
            return
        if self._cancel_recording():
            logger.info("Foreign key joined the chord, recording discarded")

    def _marker_keys(self) -> dict[int, str]:
        """Virtual-key code -> marker, from the live config. The command key wins a clash."""
        markers: dict[int, str] = {}
        for name, marker in ((self._config.paste_key, PASTE_LAST), (self._config.polish_key, POLISH)):
            if name in HOTKEY_KEY_VKS:
                markers[HOTKEY_KEY_VKS[name]] = marker
        cfg = self._command_config
        if cfg is not None and cfg.enabled and self._on_command is not None and cfg.command_key in COMMAND_KEY_VKS:
            markers[COMMAND_KEY_VKS[cfg.command_key]] = COMMAND
        markers.pop(self._key_vk, None)  # the binding's own key is never a marker
        return markers

    def _mark_recording(self, marker: str) -> None:
        if self._mark is None:
            self._mark = marker
            if marker == COMMAND:
                self._on_command()

    def _handle_marker_key(self, vk: int, marker: str | None, pressed: bool) -> bool:
        """A marker key: swallowed when it completes the chord during a recording, typed otherwise.

        It only counts with every binding modifier down, so Shift+key, AltGr+key and a
        bare key typed while a toggle recording runs still reach the application.
        """
        if not pressed:
            swallowed = vk in self._marker_swallowed
            self._marker_swallowed.discard(vk)
            return swallowed
        if vk in self._marker_swallowed:
            return True  # auto-repeat
        if marker is None:
            return False
        with self._lock:  # the grace timer finishes the recording from another thread
            if not (self._active and self._modifiers <= self._pressed_modifiers):
                return False
            self._marker_swallowed.add(vk)
            self._mark_recording(marker)
            if self.mode == "toggle" and not self._start_hold:
                if self._stop_timer is None:
                    self._stop_requested = time.monotonic()
                self._finish()  # "hotkey + marker": stop and apply the action in one gesture
            return True

    def _combo_broken(self) -> None:
        self._combo_down = False
        with self._lock:
            if self._active and self.mode == "hold":
                self._stop_requested = time.monotonic()
                self._active = False
                self._on_deactivate()

    def _handle(self, vk: int, pressed: bool, injected: bool = False) -> bool:
        """Feed one key event. Returns True if the event must be swallowed.

        `injected` (LLKHF_INJECTED) only keeps an event from discarding a hold recording; it is otherwise
        treated like a real key, since remapping tools deliver the user's keys that way.
        """
        self._sync_binding()

        if vk == _VK_ESCAPE:
            if not pressed:
                swallowed, self._escape_swallowed = self._escape_swallowed, False
                return swallowed
            if self._escape_swallowed:
                return True  # auto-repeat of the swallowed Escape
            if self._active and not self._other_modifiers_down():
                with self._lock:
                    if not self._active:
                        return False
                    self._escape_swallowed = True
                    self._cancel_recording()
                return True
            return False

        marker = self._marker_keys().get(vk)
        # A swallowed marker stays one until it is released, even if the config dropped it
        # meanwhile; otherwise its key-up would leak and the stale entry would eat a later press.
        if marker is not None or vk in self._marker_swallowed:
            return self._handle_marker_key(vk, marker, pressed)

        if vk == self._key_vk:
            if pressed:
                if self._key_swallowed:
                    return True  # auto-repeat while held as part of the combo
                self._heal_modifiers()
                if self._combo_down or not self._modifiers <= self._pressed_modifiers:
                    return False  # plain typing: the modifiers are not (all) down
                if self._extra_modifiers_down():
                    return False  # another shortcut that contains the binding: the key is not ours
                self._key_swallowed = True
                self._complete_press()
                return True
            if self._combo_down:
                self._combo_broken()
            swallowed, self._key_swallowed = self._key_swallowed, False
            return swallowed

        modifier = _VK_TO_MODIFIER.get(vk)
        if modifier is None or modifier not in self._modifiers:
            if pressed and not injected and _is_typing_key(vk):
                self._discard_on_foreign_key()
            return False  # the key itself always reaches the application
        repeat = pressed and vk in self._modifier_vks_down  # a held key repeats its key-down every ~31 ms
        if pressed and not repeat:
            self._heal_modifiers(keep=modifier)
        if pressed:
            self._modifier_vks_down.add(vk)
        else:
            self._modifier_vks_down.discard(vk)
        if pressed:
            self._pressed_modifiers.add(modifier)
            if self._combo_down and not repeat:
                self._mask_menu_keys()  # a second Alt/Win pressed during the combo
            # A modifier-only binding completes on its last modifier. With a key, the key
            # has to come last, otherwise "hold A, then press Ctrl" would start recording.
            if self._key_vk is None and not self._combo_down and self._modifiers <= self._pressed_modifiers:
                if not self._extra_modifiers_down():
                    self._complete_press()
            return False
        self._pressed_modifiers.discard(modifier)
        self._start_hold = False  # the hold that started the recording is over
        if self._combo_down:
            self._combo_broken()
        return False

    def _other_modifiers_down(self) -> bool:
        """Escape inside a shortcut (Ctrl+Shift+Esc, Alt+Esc) is not a cancel. In Hold the binding's own
        modifiers are down by design; in Toggle none should be."""
        allowed = self._modifiers if self.mode == "hold" else frozenset()
        return bool(self._physical_modifiers() - allowed)

    @property
    def stop_requested_at(self) -> float:
        """`time.monotonic()` of the latest stop chord, release or marker, before any grace window."""
        return self._stop_requested

    def _mask_menu_keys(self) -> None:
        """Type a neutral key while Alt or Win is certainly down.

        Windows opens the app's menu (Alt) or the Start menu (Win) when such a key goes up with no other
        key pressed since it went down, which is what releasing Ctrl and Shift first leaves behind. A
        keystroke in between turns the release into an ordinary chord release (AutoHotkey's menu mask
        key). It is sent when the combo completes, not at the release: from inside the hook it would
        queue behind the very key-up it must precede.
        """
        _send_menu_mask(self._modifiers, self._mask_sender)

    def _win32_event_filter(self, msg: int, data) -> None:
        """pynput hook callback, runs synchronously inside WH_KEYBOARD_LL."""
        injected = bool(getattr(data, "flags", 0) & _LLKHF_INJECTED)
        if self._handle(data.vkCode, msg in _PRESS_MESSAGES, injected) and self._listener is not None:
            self._listener.suppress_event()

    # -- state queries --------------------------------------------------------

    def cancel(self) -> None:
        """Drop the recording intent without firing on_deactivate.

        The app calls this when a cycle aborts before its recording was stopped.
        In toggle mode nothing else would ever clear `_active`: the next press
        would count as the "stop" of a recording that no longer exists, and the
        mic guard would stay muted because `still_recording()` keeps saying yes.
        """
        with self._lock:
            timer, self._stop_timer = self._stop_timer, None
            if timer is not None:
                timer.cancel()
            self._active = False

    def modifiers_physically_down(self) -> bool:
        """True while every key of the binding is physically held, asked straight from Win32.

        Independent of the pynput hook, so it still answers correctly if a key-up event
        was lost (used by the mic guard watchdog to tell a long dictation from a stuck hold).
        """
        user32 = ctypes.windll.user32
        groups: list[tuple[int, ...]] = [_MODIFIER_VKS[m] for m in self._modifiers]
        if self._key_vk is not None:
            groups.append((self._key_vk,))
        return all(any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks) for vks in groups)

    def still_recording(self) -> bool:
        """Whether the user still means to record: physical keys in hold mode, the
        toggle state in toggle mode (there the keys are up while recording)."""
        if self.mode == "toggle":
            return self._active
        return self.modifiers_physically_down()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Start listening for the hotkey. Runs the listener in a daemon thread."""
        self._mask_sender = _load_mask_sender()
        self._listener = keyboard.Listener(win32_event_filter=self._win32_event_filter)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop the hotkey listener."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def _physical_modifiers() -> set[str]:
    """The modifiers held right now, asked straight from Win32."""
    user32 = ctypes.windll.user32
    return {name for name, vks in _MODIFIER_VKS.items() if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)}


class MeetingHotkey:
    """The meeting toggle: one press of modifiers + key calls `on_toggle`, the next press calls it again.

    A listener of its own, so it never touches the dictation hotkey's state and dictation keeps working during
    a meeting. Same rules as `HotkeyListener`: decided inside the low-level hook on virtual-key codes, the key
    that completes the chord is swallowed with its repeats and key-up (the modifiers pass through), the key
    must come last, and the binding is re-read from the shared config on every event, so the dashboard is live.
    `on_toggle` runs inside the hook: it must hand the work to another thread and return.
    """

    def __init__(
        self,
        config: MeetingConfig,
        on_toggle: Callable[[], None],
        physical_modifiers: Callable[[], set[str]] | None = None,
    ) -> None:
        self._config = config
        self._on_toggle = on_toggle
        self._physical_modifiers = physical_modifiers or _physical_modifiers
        self._pressed_modifiers: set[str] = set()
        self._key_swallowed = False  # the key is held since a swallowed key-down
        self._mask_sender: Callable[[], object] | None = None  # set by start(); tests leave it unset
        self._listener: keyboard.Listener | None = None

    def _handle(self, vk: int, pressed: bool) -> bool:
        """Feed one key event. Returns True if the event must be swallowed."""
        key_vk = HOTKEY_KEY_VKS.get(self._config.key)
        if vk == key_vk:
            if pressed:
                if self._key_swallowed:
                    return True  # auto-repeat while held as part of the chord
                # The tracked set is only a hint: a key-up the hook never saw (Ctrl+Alt+Del, a lock screen) would
                # leave a modifier counted as held, so the physical state decides and heals the set. The chord
                # must match exactly: with more modifiers down it is another shortcut.
                self._pressed_modifiers &= self._physical_modifiers()
                if self._pressed_modifiers != set(self._config.modifiers):
                    return False  # plain typing or another chord
                self._key_swallowed = True
                # The chord key is swallowed, so Alt or Win is released after a keystroke nobody saw: without a
                # mask key the app's menu (Alt) or the Start menu (Win) would open on every toggle.
                _send_menu_mask(self._pressed_modifiers, self._mask_sender)
                self._on_toggle()
                return True
            swallowed, self._key_swallowed = self._key_swallowed, False
            return swallowed
        modifier = _VK_TO_MODIFIER.get(vk)
        if modifier is not None:
            if pressed:
                self._pressed_modifiers.add(modifier)
            else:
                self._pressed_modifiers.discard(modifier)
        return False

    def _win32_event_filter(self, msg: int, data) -> None:
        """pynput hook callback, runs synchronously inside WH_KEYBOARD_LL."""
        if self._handle(data.vkCode, msg in _PRESS_MESSAGES) and self._listener is not None:
            self._listener.suppress_event()

    def start(self) -> None:
        self._mask_sender = _load_mask_sender()
        self._listener = keyboard.Listener(win32_event_filter=self._win32_event_filter)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def describe_hotkey(config: HotkeyConfig | MeetingConfig) -> str:
    """Human-readable binding, e.g. "Ctrl+Shift+A" or "Ctrl+Shift+Alt"."""
    parts = [m.capitalize() for m in config.modifiers]
    if config.key:
        parts.append(config.key.replace("_", " ").title() if len(config.key) > 1 else config.key)
    return "+".join(parts)
