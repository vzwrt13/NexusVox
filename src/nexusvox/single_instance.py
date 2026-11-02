r"""One NexusVox per user session: a second start hands over to the running one and exits.

Two named kernel objects do the work: a mutex says "an instance runs", an event is the doorbell. The
second start finds the mutex taken, rings the doorbell (the running instance opens its dashboard) and
exits before it builds a tray icon or installs the hotkey hook. The names are per session
(``Local\``), so two Windows users each get their own NexusVox.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

MUTEX_NAME = "Local\\NexusVox-single-instance"
EVENT_NAME = "Local\\NexusVox-open-dashboard"

_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_POLL_MS = 500


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


class SingleInstance:
    """Holds the instance mutex and the dashboard doorbell for the life of the process."""

    def __init__(self) -> None:
        self._mutex = None
        self._event = None
        self._stop = threading.Event()

    def acquire(self) -> bool:
        """True when this process is the only instance. Always True where named mutexes do not exist."""
        if sys.platform != "win32":
            return True
        k32 = _kernel32()
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateEventW.restype = ctypes.c_void_p
        mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
        if not mutex:
            logger.warning("Could not create the single-instance mutex; starting without the check")
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            k32.CloseHandle(ctypes.c_void_p(mutex))
            return False
        self._mutex = mutex
        self._event = k32.CreateEventW(None, False, False, EVENT_NAME)  # auto-reset
        return True

    def ring_running_instance(self) -> bool:
        """Ask the running instance to open its dashboard. False when it cannot be reached."""
        if sys.platform != "win32":
            return False
        k32 = _kernel32()
        k32.OpenEventW.restype = ctypes.c_void_p
        handle = k32.OpenEventW(_EVENT_MODIFY_STATE, False, EVENT_NAME)
        if not handle:
            return False
        try:
            return bool(k32.SetEvent(ctypes.c_void_p(handle)))
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))

    def watch(self, on_ring: Callable[[], None]) -> None:
        """Call `on_ring` (from a daemon thread) each time a second start rings the doorbell."""
        if self._event is None:
            return
        threading.Thread(target=self._wait_loop, args=(on_ring,), name="single-instance", daemon=True).start()

    def _wait_loop(self, on_ring: Callable[[], None]) -> None:
        k32 = _kernel32()
        event = ctypes.c_void_p(self._event)
        while not self._stop.is_set():
            if k32.WaitForSingleObject(event, _POLL_MS) == _WAIT_OBJECT_0:
                try:
                    on_ring()
                except Exception:
                    logger.exception("Could not open the dashboard for a second start")

    def release(self) -> None:
        self._stop.set()


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
