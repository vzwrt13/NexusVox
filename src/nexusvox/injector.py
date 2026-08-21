"""Text injection at the active cursor position via clipboard paste."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

import pyperclip

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

# Hardware scan codes — required for apps that ignore virtual-key-only input
SCAN_CONTROL = 0x1D
SCAN_V = 0x2F
SCAN_SHIFT = 0x2A
SCAN_ALT = 0x38
SCAN_LWIN = 0x5B

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


def _send_ctrl_v() -> bool:
    """Simulate Ctrl+V keypress via SendInput with hardware scan codes.

    Attaches to the foreground window's thread input queue first so that
    Windows accepts the synthetic input even from a background thread.
    Returns True if all events were injected successfully.
    """
    hwnd = user32.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    attached = False
    if hwnd and current_thread != target_thread:
        attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))

    inputs = [
        _make_key_input(VK_CONTROL, SCAN_CONTROL),  # Ctrl down
        _make_key_input(VK_V, SCAN_V),  # V down
        _make_key_input(VK_V, SCAN_V, KEYEVENTF_KEYUP),  # V up
        _make_key_input(VK_CONTROL, SCAN_CONTROL, KEYEVENTF_KEYUP),  # Ctrl up
    ]
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))

    if attached:
        user32.AttachThreadInput(current_thread, target_thread, False)

    logger.info("SendInput Ctrl+V: sent %d/%d events (attached=%s)", sent, len(inputs), attached)
    if sent == 0:
        logger.error("SendInput returned 0 — blocked by UIPI or thread not attached")
    return sent == len(inputs)


def _paste_via_message() -> bool:
    """Send WM_PASTE to the focused control in the foreground window.

    Bypasses UIPI since WM_PASTE is a window message, not synthetic input.
    Returns True if the message was posted successfully.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        logger.error("No foreground window found")
        return False

    # Get the focused control within the foreground window.
    # GetFocus() only works within our own thread, so we must temporarily
    # attach to the target window's thread input queue.
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    focused = None
    if current_thread != target_thread:
        if user32.AttachThreadInput(current_thread, target_thread, True):
            focused = user32.GetFocus()
            user32.AttachThreadInput(current_thread, target_thread, False)

    target = focused or hwnd
    result = user32.PostMessageW(target, WM_PASTE, 0, 0)
    logger.info(
        "WM_PASTE posted to %s (hwnd=%s): %s",
        "focused control" if focused else "foreground window",
        hex(target),
        "OK" if result else "FAILED",
    )
    return bool(result)


def inject_text(text: str, injection_delay_ms: int = 500) -> None:
    """Inject text at the active cursor position using clipboard paste.

    Saves the current clipboard, sets new content, pastes, then restores.
    ``injection_delay_ms`` is the pause between releasing modifier keys and
    sending the Ctrl+V keystroke, giving the OS time to clear stale state.
    """
    # Save current clipboard
    try:
        original = pyperclip.paste()
    except pyperclip.PyperclipException:
        original = None

    try:
        # Set transcription text to clipboard
        pyperclip.copy(text)
        # Small delay to ensure clipboard is set
        time.sleep(0.05)
        # Release any modifier keys still held from the push-to-talk hotkey
        # (Ctrl+Shift+Alt) to prevent sending Ctrl+Shift+Alt+V instead of Ctrl+V
        _release_all_modifiers()
        time.sleep(injection_delay_ms / 1000)
        # Try SendInput Ctrl+V first (works for browsers and most apps),
        # fall back to WM_PASTE (works for native Win32 controls like Notepad)
        if not _send_ctrl_v():
            logger.info("SendInput failed, falling back to WM_PASTE")
            _paste_via_message()
        # Wait for paste to complete
        time.sleep(0.05)
    finally:
        # Restore original clipboard
        if original is not None:
            time.sleep(0.05)
            try:
                pyperclip.copy(original)
            except pyperclip.PyperclipException:
                pass
