r"""One NexusVox per user session: a second start hands over to the running one and exits.

Two named kernel objects do the work: a mutex says "an instance runs", an event is the doorbell. The
second start finds the mutex taken, rings the doorbell (the running instance opens its dashboard) and
exits before it builds a tray icon or installs the hotkey hook. The names are per session
(``Local\``), so two Windows users each get their own NexusVox.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import sys
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

MUTEX_NAME = "Local\\NexusVox-single-instance"
EVENT_NAME = "Local\\NexusVox-open-dashboard"

_ERROR_ACCESS_DENIED = 5
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0
_POLL_MS = 500
_SDDL_REVISION_1 = 1

# Who may open the mutex. The installer checks it (Inno Setup's AppMutex, SYNCHRONIZE access) to refuse an install
# or uninstall over a running NexusVox. An elevated NexusVox would otherwise own a mutex that a normal-rights Setup
# cannot open, and Setup would read "access denied" as "not running". Full control stays with the owner, SYSTEM and
# the Administrators; everybody else who is logged on may only wait on it (0x00100000 = SYNCHRONIZE).
_MUTEX_SDDL = "D:(A;;GA;;;OW)(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100000;;;AU)"


@functools.cache
def _kernel32():
    """kernel32 with the prototypes declared once, so a 64-bit handle is never truncated to an int."""
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateEventW.restype = ctypes.c_void_p
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    k32.SetEvent.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    return k32


class _SecurityAttributes(ctypes.Structure):
    _fields_ = (
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int32),
    )


def _create_mutex(k32):
    """CreateMutexW with `_MUTEX_SDDL` as its security; the default security if the descriptor cannot be built."""
    descriptor = ctypes.c_void_p()
    convert = ctypes.WinDLL("advapi32", use_last_error=True).ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = ctypes.c_int32
    if not convert(_MUTEX_SDDL, _SDDL_REVISION_1, ctypes.byref(descriptor), None):
        logger.warning("Could not build the mutex security descriptor (error %d)", ctypes.get_last_error())
        return k32.CreateMutexW(None, False, MUTEX_NAME)
    try:
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, 0)
        return k32.CreateMutexW(ctypes.byref(attributes), False, MUTEX_NAME)
    finally:
        # CreateMutexW copies the descriptor, and the caller reads GetLastError after this returns. LocalFree
        # leaves the last error alone on success.
        k32.LocalFree(descriptor)


class SingleInstance:
    """Holds the instance mutex and the dashboard doorbell for the life of the process."""

    def __init__(self) -> None:
        self._mutex = None
        self._event = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def acquire(self) -> bool:
        """True when this process is the only instance. Always True where named mutexes do not exist."""
        if sys.platform != "win32":
            return True
        k32 = _kernel32()
        # The doorbell first: whoever sees the mutex can then always find the event.
        event = k32.CreateEventW(None, False, False, EVENT_NAME)  # auto-reset
        if not event:
            logger.warning("Could not create the dashboard doorbell (error %d)", ctypes.get_last_error())
        mutex = _create_mutex(k32)
        error = ctypes.get_last_error()
        if not mutex:
            if event:
                k32.CloseHandle(event)
            if error == _ERROR_ACCESS_DENIED:  # the running instance is elevated and we are not
                return False
            logger.warning("Could not create the single-instance mutex (error %d); starting without it", error)
            return True
        if error == _ERROR_ALREADY_EXISTS:
            k32.CloseHandle(mutex)
            if event:
                k32.CloseHandle(event)
            return False
        self._mutex = mutex
        self._event = event
        return True

    def ring_running_instance(self) -> bool:
        """Ask the running instance to open its dashboard. False when it cannot be reached."""
        if sys.platform != "win32":
            return False
        k32 = _kernel32()
        handle = k32.OpenEventW(_EVENT_MODIFY_STATE, False, EVENT_NAME)
        if not handle:
            return False
        try:
            return bool(k32.SetEvent(handle))
        finally:
            k32.CloseHandle(handle)

    def watch(self, on_ring: Callable[[], None]) -> None:
        """Call `on_ring` (from a daemon thread) each time a second start rings the doorbell."""
        if self._event is None:
            return
        self._thread = threading.Thread(target=self._wait_loop, args=(on_ring,), name="single-instance", daemon=True)
        self._thread.start()

    def _wait_loop(self, on_ring: Callable[[], None]) -> None:
        k32 = _kernel32()
        while not self._stop.is_set():
            if k32.WaitForSingleObject(self._event, _POLL_MS) == _WAIT_OBJECT_0 and not self._stop.is_set():
                try:
                    on_ring()
                except Exception:
                    logger.exception("Could not open the dashboard for a second start")

    def release(self) -> None:
        """Stop watching and give the mutex and the doorbell back, so a restart can take over."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=(_POLL_MS / 1000) + 1)
            if self._thread.is_alive():  # still inside a handle wait: leaking beats closing it underneath
                return
            self._thread = None
        if sys.platform != "win32":
            return
        k32 = _kernel32()
        for name in ("_event", "_mutex"):
            handle = getattr(self, name)
            if handle:
                k32.CloseHandle(handle)
                setattr(self, name, None)


def show_message(text: str, title: str = "NexusVox") -> None:
    """A message box that does not block the caller; the log line is the fallback off Windows."""
    if sys.platform != "win32":
        return
    try:
        threading.Thread(
            target=lambda: ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x10000),
            name="message-box",
            daemon=True,
        ).start()
    except Exception:  # a message is a courtesy; the log has the same text
        logger.debug("Could not show the message box", exc_info=True)
