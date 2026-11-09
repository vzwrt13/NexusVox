"""Text injection at the active cursor position via clipboard paste."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

import pyperclip

from ..system.keyboard import wait_for_physical_release
from .clipboard import ClipboardError, restore_formats, set_text, snapshot_formats

logger = logging.getLogger("nexusvox.injector")

user32 = ctypes.windll.user32

# SendInput constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_V = 0x56
VK_C = 0x43
VK_RETURN = 0x0D
VK_MENU_MASK = 0xE8  # unassigned virtual key, the one AutoHotkey uses as its menu mask key

# Hardware scan codes — required for apps that ignore virtual-key-only input
SCAN_CONTROL = 0x1D
SCAN_V = 0x2F
SCAN_C = 0x2E
SCAN_RETURN = 0x1C
SCAN_SHIFT = 0x2A
SCAN_ALT = 0x38
SCAN_LWIN = 0x5B

VK_RWIN = 0x5C

# Modifiers that may still be physically held when the transcription arrives

# Pause between the paste and putting the user's clipboard back (about 300 ms after Ctrl+V)
_RESTORE_DELAY_S = 0.25

# Window message for paste
WM_PASTE = 0x0302


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    """Needed in the INPUT union so ctypes computes the correct struct size."""

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def _make_key_input(vk: int, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.wScan = scan
    inp._input.ki.dwFlags = flags
    return inp


def menu_mask_inputs() -> list[INPUT]:
    """A keystroke on a neutral key. Sent while Alt is down, it tells Windows the Alt press was part of a
    chord, so the later Alt release does not count as a lone Alt tap that opens the app's menu."""
    return [_make_key_input(VK_MENU_MASK), _make_key_input(VK_MENU_MASK, 0, KEYEVENTF_KEYUP)]


def _send_inputs(inputs: list[INPUT], name: str) -> bool:
    """SendInput a batch; logs when Windows injects fewer events than asked (UIPI, elevated target)."""
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    logger.debug("SendInput %s: sent %d/%d events", name, sent, len(inputs))
    if sent != len(inputs):
        logger.error("SendInput %s: sent %d/%d events (blocked by UIPI?)", name, sent, len(inputs))
    return sent == len(inputs)


class PasteError(ClipboardError):
    """No way of pasting reached the target window (an elevated app, or none in the foreground).

    A `ClipboardError` on purpose: every `inject_text` caller already answers it with the error cue and
    keeps the text for the paste key.
    """


def send_menu_mask() -> bool:
    """Send the menu mask keystroke now (see `menu_mask_inputs`)."""
    return _send_inputs(menu_mask_inputs(), "menu mask")


# Generic modifier -> every virtual key that counts as it being held
_MODIFIER_GROUPS = (
    (VK_CONTROL, SCAN_CONTROL, (VK_CONTROL, 0xA2, 0xA3)),
    (VK_SHIFT, SCAN_SHIFT, (VK_SHIFT, 0xA0, 0xA1)),
    (VK_MENU, SCAN_ALT, (VK_MENU, 0xA4, 0xA5)),
    (VK_LWIN, SCAN_LWIN, (VK_LWIN, VK_RWIN)),
)


def _held_modifiers() -> set[int]:
    """The generic modifier keys (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN) that are down right now."""
    return {
        generic for generic, _scan, vks in _MODIFIER_GROUPS if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)
    }


def _release_inputs(held: set[int]) -> list[INPUT]:
    """Key-ups for the modifiers in `held` only; with Alt or Win among them, the menu mask goes in before
    the first such key-up.

    A key-up for a key that is not down is never sent: for Alt it is a lone Alt release, which menu-bar
    apps (Notepad, Word) answer by toggling their menu on or off, and for Win it opens the Start menu.
    """
    inputs: list[INPUT] = []
    masked = False
    for vk, sc, _vks in _MODIFIER_GROUPS:
        if vk not in held:
            continue
        if vk in (VK_MENU, VK_LWIN) and not masked:
            inputs.extend(menu_mask_inputs())
            masked = True
        if (
            vk == VK_LWIN
            and not user32.GetAsyncKeyState(VK_LWIN) & 0x8000
            and user32.GetAsyncKeyState(VK_RWIN) & 0x8000
        ):
            vk = VK_RWIN  # only Right Win is down: a key-up for Left Win would leave it held
        inputs.append(_make_key_input(vk, sc, KEYEVENTF_KEYUP))
    return inputs


def _release_all_modifiers() -> None:
    """Release the modifier keys that are still down, to clear stale state from the push-to-talk hotkey.

    Does nothing when none is down, which is the normal case after `wait_for_physical_release`.
    """
    held = _held_modifiers()
    if held:
        _send_inputs(_release_inputs(held), "modifier release")


def _send_ctrl_chord(vk: int, scan: int, name: str) -> bool:
    """Simulate Ctrl+<key> via SendInput with hardware scan codes.

    Deliberately does NOT call AttachThreadInput: SendInput does not need it,
    and attaching/detaching around the call races with the target app
    processing the keydown. When the detach re-synchronises the target
    thread's key state before the app reads GetKeyState(VK_CONTROL), the app
    sees a plain "v" instead of a paste.
    Returns True if all events were injected successfully.
    """
    inputs = [
        _make_key_input(VK_CONTROL, SCAN_CONTROL),  # Ctrl down
        _make_key_input(vk, scan),  # key down
        _make_key_input(vk, scan, KEYEVENTF_KEYUP),  # key up
        _make_key_input(VK_CONTROL, SCAN_CONTROL, KEYEVENTF_KEYUP),  # Ctrl up
    ]
    return _send_inputs(inputs, f"Ctrl+{name}")


def _send_ctrl_v() -> bool:
    return _send_ctrl_chord(VK_V, SCAN_V, "V")


def _focused_control(hwnd: int) -> int | None:
    """Return the focused child control of ``hwnd``'s thread via GetGUIThreadInfo.

    Unlike the AttachThreadInput + GetFocus trick this does not touch the
    target thread's input queue or key state.
    """
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if target_thread and user32.GetGUIThreadInfo(target_thread, ctypes.byref(info)):
        return info.hwndFocus or None
    return None


def _paste_via_message() -> bool:
    """Post WM_PASTE to the focused control in the foreground window.

    Works for native Win32 controls that ignore synthetic keystrokes. It is no way around UIPI: a
    window message to an elevated window is blocked just like SendInput, and `PostMessageW` then
    returns 0. Returns True if the message was posted.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        logger.error("No foreground window found")
        return False

    focused = _focused_control(hwnd)
    target = focused or hwnd
    result = user32.PostMessageW(target, WM_PASTE, 0, 0)
    where = "focused control" if focused else "foreground window"
    if result:
        logger.info("WM_PASTE posted to %s (hwnd=%s)", where, hex(target))
    else:
        logger.error(
            "WM_PASTE to %s (hwnd=%s) failed (error %d): the target runs elevated and blocks window messages too",
            where,
            hex(target),
            ctypes.get_last_error(),
        )
    return bool(result)


def _send_enter() -> bool:
    """Simulate an Enter keypress via SendInput. Returns True if both events were injected."""
    inputs = [
        _make_key_input(VK_RETURN, SCAN_RETURN),
        _make_key_input(VK_RETURN, SCAN_RETURN, KEYEVENTF_KEYUP),
    ]
    return _send_inputs(inputs, "Enter")


def _prepare_keyboard(injection_delay_ms: int) -> None:
    """Wait until the user lets go of the hotkey, clear stale modifier state, then pause.

    The hotkey fires on the first modifier going up; without this a synthetic
    keystroke would be combined with the still-held modifiers (Ctrl+V turning
    into Ctrl+Win+V, Enter into Ctrl+Enter).
    """
    wait_for_physical_release()
    _release_all_modifiers()
    time.sleep(injection_delay_ms / 1000)


def foreground_window() -> int:
    """The handle of the window that has the keyboard focus now (0 when none)."""
    return user32.GetForegroundWindow() or 0


def _save_clipboard() -> str | list[tuple[int, bytes]] | None:
    """What is on the clipboard now, to put back later: its text, else every format it holds.

    `pyperclip.paste()` answers "" for an image or a file list, and putting "" back would wipe it, so an
    empty answer takes a snapshot of all formats. None when the clipboard cannot be read.
    """
    try:
        text = pyperclip.paste()
    except pyperclip.PyperclipException:
        return None
    return text or snapshot_formats()


def _restore_clipboard(saved: str | list[tuple[int, bytes]] | None) -> None:
    """Put back what `_save_clipboard` returned; a clipboard that cannot be written stays as it is."""
    if saved is None:
        return
    try:
        if isinstance(saved, str):
            set_text(saved)
        else:
            restore_formats(saved)
    except ClipboardError:
        pass


def _is_whole_line_copy(text: str) -> bool:
    """True for exactly one line plus its line break: what an editor copies when nothing is selected.

    VS Code (and other editors) answer Ctrl+C with no selection by copying the caret's whole line,
    break included, so the clipboard changes although the user selected nothing. A real selection of
    one line that includes its break looks the same and is not told apart.
    """
    body = text.rstrip("\r\n")
    return 0 < len(text) - len(body) <= 2 and "\n" not in body and "\r" not in body


def copy_selection(injection_delay_ms: int = 500, timeout_s: float = 1.0) -> str:
    """The text selected in the focused app, copied with Ctrl+C; "" when nothing is selected.

    The clipboard is emptied first, so an unchanged empty clipboard after Ctrl+C means
    there was no selection, and it is put back as it was afterwards (an image or a file
    list included). A whole line copied without any selection counts as no selection.
    """
    original = _save_clipboard()
    try:
        set_text("")
        _prepare_keyboard(injection_delay_ms)
        if not _send_ctrl_chord(VK_C, SCAN_C, "C"):
            return ""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(0.05)
            try:
                selected = pyperclip.paste()
            except pyperclip.PyperclipException:
                continue
            if selected:
                if _is_whole_line_copy(selected):
                    logger.info("Ctrl+C copied a whole line: treated as no selection")
                    return ""
                return selected
        return ""
    finally:
        _restore_clipboard(original)


def inject_text(text: str, injection_delay_ms: int = 500, press_enter: bool = False) -> float:
    """Inject text at the active cursor position using clipboard paste.

    Saves the current clipboard, sets new content, pastes, then restores (all formats of an image
    or file clipboard, not just text). Both writes stay out of the Windows clipboard history
    (`clipboard.set_text`). Raises `PasteError` when no paste reached the target (an elevated window).
    ``injection_delay_ms`` is the pause between releasing modifier keys and
    sending the Ctrl+V keystroke, giving the OS time to clear stale state.
    With ``press_enter`` an Enter keypress follows the paste; an empty ``text``
    then presses Enter only.

    Returns the `time.monotonic()` at which the text was in the target, before the wait
    for putting the clipboard back, so the caller's latency is not the restore delay.
    """
    if not text:
        if press_enter:
            _prepare_keyboard(injection_delay_ms)
            _send_enter()
        return time.monotonic()

    original = _save_clipboard()

    clipboard_changed = False
    try:
        # Set transcription text to clipboard
        set_text(text)
        clipboard_changed = True
        # Small delay to ensure clipboard is set
        time.sleep(0.05)
        _prepare_keyboard(injection_delay_ms)
        # Try SendInput Ctrl+V first (works for browsers and most apps),
        # fall back to WM_PASTE (works for native Win32 controls like Notepad)
        if not _send_ctrl_v():
            logger.info("SendInput failed, falling back to WM_PASTE")
            if not _paste_via_message():
                raise PasteError("neither SendInput nor WM_PASTE reached the target window (running elevated?)")
        # Wait for paste to complete
        time.sleep(0.05)
        if press_enter:
            # Give the target time to insert the pasted text before Enter submits it.
            time.sleep(0.1)
            _send_enter()
        pasted_at = time.monotonic()
    finally:
        # Restore original clipboard (untouched when set_text failed: nothing to restore)
        if original is not None and clipboard_changed:
            # Electron apps and terminals read the clipboard well after Ctrl+V; restoring
            # sooner made them paste the old clipboard instead of the dictation.
            time.sleep(_RESTORE_DELAY_S)
            _restore_clipboard(original)
    return pasted_at
