"""Win32 window management for nexus OS commands."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# --- Constants ---

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_MENU = 0x12  # Alt
VK_F11 = 0x7A
VK_LWIN = 0x5B
VK_LEFT = 0x25
VK_RIGHT = 0x27

SCAN_ALT = 0x38
SCAN_LWIN = 0x5B
SCAN_F11 = 0x57
SCAN_LEFT = 0x4B
SCAN_RIGHT = 0x4D

WM_CLOSE = 0x0010

SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5

SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
SPIF_SENDCHANGE = 0x02

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# EnumWindows callback type
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# --- ctypes structs (mirrored from injector.py to keep modules decoupled) ---


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
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


# --- Helpers ---


def _make_key_input(vk: int, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.wScan = scan
    inp._input.ki.dwFlags = flags
    return inp


def _send_keys(inputs: list[INPUT]) -> int:
    array = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _press_release_key(vk: int, scan: int = 0) -> None:
    _send_keys(
        [
            _make_key_input(vk, scan),
            _make_key_input(vk, scan, KEYEVENTF_KEYUP),
        ]
    )


def _press_release_alt() -> None:
    _press_release_key(VK_MENU, SCAN_ALT)


# --- Window finding ---


def _get_process_exe(pid: int) -> str | None:
    """Get the executable path for a process ID."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def find_window(app_name: str, exe_path: str | None = None) -> int | None:
    """Find a visible top-level window belonging to an app.

    Matches by checking if the process executable basename contains the app alias,
    or if the window title contains the app name as a fallback.
    """
    result: list[int] = []

    # Derive expected exe basename from the configured path
    if exe_path:
        exe_basename = os.path.basename(exe_path).lower()
    else:
        exe_basename = app_name.lower()

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True  # continue enumeration

        # Try matching by process executable
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_exe = _get_process_exe(pid.value)
        if proc_exe:
            proc_basename = os.path.basename(proc_exe).lower()
            if exe_basename in proc_basename or app_name.lower() in proc_basename:
                result.append(hwnd)
                return False  # stop enumeration

        # Fallback: match by window title
        title_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, 256)
        title = title_buf.value.lower()
        if app_name.lower() in title:
            result.append(hwnd)
            return False

        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result[0] if result else None


# --- Force foreground ---


def force_foreground(hwnd: int) -> bool:
    """Force a window to the foreground, working around Vista+ restrictions.

    Strategy: AttachThreadInput + Alt key trick + SetForegroundWindow,
    with SPI_SETFOREGROUNDLOCKTIMEOUT fallback.
    """
    current_thread = kernel32.GetCurrentThreadId()
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    # Restore if minimized
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    # Attach to foreground and target threads
    attached_fg = False
    attached_target = False
    if current_thread != fg_thread:
        attached_fg = bool(user32.AttachThreadInput(current_thread, fg_thread, True))
    if current_thread != target_thread and fg_thread != target_thread:
        attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))

    # Alt key trick — satisfies the "user initiated" requirement
    _press_release_alt()
    time.sleep(0.05)

    # Bring to front
    user32.BringWindowToTop(hwnd)
    ok = bool(user32.SetForegroundWindow(hwnd))

    # Detach
    if attached_fg:
        user32.AttachThreadInput(current_thread, fg_thread, False)
    if attached_target:
        user32.AttachThreadInput(current_thread, target_thread, False)

    # Fallback: temporarily zero the foreground lock timeout
    if not ok:
        logger.debug("SetForegroundWindow failed, trying SPI timeout fallback")
        old_timeout = wintypes.DWORD()
        ctypes.windll.user32.SystemParametersInfoW(
            0x2000,
            0,
            ctypes.byref(old_timeout),
            0,  # SPI_GETFOREGROUNDLOCKTIMEOUT
        )
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None, SPIF_SENDCHANGE)
        user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.cast(old_timeout.value, ctypes.c_void_p), SPIF_SENDCHANGE
        )
        ok = bool(user32.GetForegroundWindow() == hwnd)

    logger.info("force_foreground hwnd=%s: %s", hex(hwnd), "OK" if ok else "FAILED")
    return ok


# --- Public API ---


def open_app(exe_path: str) -> None:
    """Launch an application."""
    logger.info("Opening: %s", exe_path)
    subprocess.Popen(
        exe_path,
        shell=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def close_app(app_name: str, exe_path: str) -> None:
    """Close an application gracefully via WM_CLOSE, falling back to taskkill."""
    hwnd = find_window(app_name, exe_path)
    if not hwnd:
        logger.warning("close_app: no window found for '%s'", app_name)
        return

    logger.info("Closing '%s' (hwnd=%s) via WM_CLOSE", app_name, hex(hwnd))
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    # Give the app time to close gracefully
    time.sleep(0.5)

    # Check if still alive
    if user32.IsWindow(hwnd):
        logger.info("Window still alive, falling back to taskkill")
        exe_basename = os.path.basename(exe_path) if exe_path else f"{app_name}.exe"
        subprocess.run(
            ["taskkill", "/f", "/im", exe_basename],
            capture_output=True,
            text=True,
        )


def focus_app(app_name: str, exe_path: str | None = None) -> bool:
    """Bring an app's window to the foreground."""
    hwnd = find_window(app_name, exe_path)
    if not hwnd:
        logger.warning("focus_app: no window found for '%s'", app_name)
        return False
    return force_foreground(hwnd)


def fullscreen_app(app_name: str, exe_path: str | None = None) -> None:
    """Focus the app and send F11 to toggle fullscreen."""
    if not focus_app(app_name, exe_path):
        return
    time.sleep(0.1)
    _press_release_key(VK_F11, SCAN_F11)
    logger.info("Sent F11 to '%s'", app_name)


def minimize_app(app_name: str, exe_path: str | None = None) -> None:
    """Minimize an app's window."""
    hwnd = find_window(app_name, exe_path)
    if not hwnd:
        logger.warning("minimize_app: no window found for '%s'", app_name)
        return
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    logger.info("Minimized '%s'", app_name)


def snap_app(app_name: str, direction: str, exe_path: str | None = None) -> None:
    """Snap an app's window left or right (Win+Arrow)."""
    if not focus_app(app_name, exe_path):
        return
    time.sleep(0.1)

    vk_arrow = VK_LEFT if direction == "left" else VK_RIGHT
    scan_arrow = SCAN_LEFT if direction == "left" else SCAN_RIGHT

    _send_keys(
        [
            _make_key_input(VK_LWIN, SCAN_LWIN),  # Win down
            _make_key_input(vk_arrow, scan_arrow),  # Arrow down
            _make_key_input(vk_arrow, scan_arrow, KEYEVENTF_KEYUP),  # Arrow up
            _make_key_input(VK_LWIN, SCAN_LWIN, KEYEVENTF_KEYUP),  # Win up
        ]
    )
    logger.info("Snapped '%s' %s", app_name, direction)
