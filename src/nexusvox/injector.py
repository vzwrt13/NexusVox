"""Text injection at the active cursor position via clipboard paste."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

import pyperclip

from .clipboard import ClipboardError, set_text

logger = logging.getLogger(__name__)

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
_MODIFIER_VKS = (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)

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


def _release_all_modifiers() -> None:
    """Release all modifier keys to clear stale state from the push-to-talk hotkey."""
    mods = [
        (VK_CONTROL, SCAN_CONTROL),
        (VK_SHIFT, SCAN_SHIFT),
        (VK_MENU, SCAN_ALT),
        (VK_LWIN, SCAN_LWIN),
    ]
    inputs = [_make_key_input(vk, sc, KEYEVENTF_KEYUP) for vk, sc in mods]
    array = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _wait_for_physical_release(timeout_s: float = 1.5) -> bool:
    """Block until the user has physically released every modifier key.

    The hotkey deactivates as soon as the *first* modifier goes up, so with a
    fast backend the transcription can be ready while Ctrl or Shift is still
    held. Pasting at that moment would send Ctrl+Shift+V (or worse). Returns
    True if all modifiers are up, False on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS):
            return True
        time.sleep(0.01)
    logger.warning("Modifier keys still held after %.1fs, forcing synthetic release", timeout_s)
    return False


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
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))

    logger.info("SendInput Ctrl+%s: sent %d/%d events", name, sent, len(inputs))
    if sent == 0:
        logger.error("SendInput returned 0 — blocked by UIPI (target runs elevated?)")
    return sent == len(inputs)


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
    """Send WM_PASTE to the focused control in the foreground window.

    Bypasses UIPI since WM_PASTE is a window message, not synthetic input.
    Returns True if the message was posted successfully.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        logger.error("No foreground window found")
        return False

    focused = _focused_control(hwnd)
    target = focused or hwnd
    result = user32.PostMessageW(target, WM_PASTE, 0, 0)
    logger.info(
        "WM_PASTE posted to %s (hwnd=%s): %s",
        "focused control" if focused else "foreground window",
        hex(target),
        "OK" if result else "FAILED",
    )
    return bool(result)


def _send_enter() -> bool:
    """Simulate an Enter keypress via SendInput. Returns True if both events were injected."""
    inputs = [
        _make_key_input(VK_RETURN, SCAN_RETURN),
        _make_key_input(VK_RETURN, SCAN_RETURN, KEYEVENTF_KEYUP),
    ]
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    logger.info("SendInput Enter: sent %d/%d events", sent, len(inputs))
    if sent == 0:
        logger.error("SendInput returned 0 for Enter — blocked by UIPI (target runs elevated?)")
    return sent == len(inputs)


def _prepare_keyboard(injection_delay_ms: int) -> None:
    """Wait until the user lets go of the hotkey, clear stale modifier state, then pause.

    The hotkey fires on the first modifier going up; without this a synthetic
    keystroke would be combined with the still-held modifiers (Ctrl+V turning
    into Ctrl+Shift+Alt+V, Enter into Ctrl+Enter).
    """
    _wait_for_physical_release()
    _release_all_modifiers()
    time.sleep(injection_delay_ms / 1000)


def foreground_window() -> int:
    """The handle of the window that has the keyboard focus now (0 when none)."""
    return user32.GetForegroundWindow() or 0


def copy_selection(injection_delay_ms: int = 500, timeout_s: float = 1.0) -> str:
    """The text selected in the focused app, copied with Ctrl+C; "" when nothing is selected.

    The clipboard is emptied first, so an unchanged empty clipboard after Ctrl+C means
    there was no selection, and it is put back as it was afterwards.
    """
    try:
        original = pyperclip.paste()
    except pyperclip.PyperclipException:
        original = None
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
                return selected
        return ""
    finally:
        if original is not None:
            try:
                set_text(original)
            except ClipboardError:
                pass


def inject_text(text: str, injection_delay_ms: int = 500, press_enter: bool = False) -> None:
    """Inject text at the active cursor position using clipboard paste.

    Saves the current clipboard, sets new content, pastes, then restores. Both writes
    stay out of the Windows clipboard history (`clipboard.set_text`).
    ``injection_delay_ms`` is the pause between releasing modifier keys and
    sending the Ctrl+V keystroke, giving the OS time to clear stale state.
    With ``press_enter`` an Enter keypress follows the paste; an empty ``text``
    then presses Enter only.
    """
    if not text:
        if press_enter:
            _prepare_keyboard(injection_delay_ms)
            _send_enter()
        return

    # Save current clipboard
    try:
        original = pyperclip.paste()
    except pyperclip.PyperclipException:
        original = None

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
            _paste_via_message()
        # Wait for paste to complete
        time.sleep(0.05)
        if press_enter:
            # Give the target time to insert the pasted text before Enter submits it.
            time.sleep(0.1)
            _send_enter()
    finally:
        # Restore original clipboard (untouched when set_text failed: nothing to restore)
        if original is not None and clipboard_changed:
            # Electron apps and terminals read the clipboard well after Ctrl+V; restoring
            # sooner made them paste the old clipboard instead of the dictation.
            time.sleep(_RESTORE_DELAY_S)
            try:
                set_text(original)
            except ClipboardError:
                pass
