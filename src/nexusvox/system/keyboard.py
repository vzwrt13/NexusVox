"""The physical state of the keyboard, for code about to send keys of its own."""

from __future__ import annotations

import ctypes
import logging
import time

# The name this has always logged under: log lines print it.
logger = logging.getLogger("nexusvox.injector")

user32 = ctypes.windll.user32

_MODIFIER_VKS = (0x11, 0x10, 0x12, 0x5B, 0x5C)  # Ctrl, Shift, Alt, left Win, right Win


def wait_for_physical_release(timeout_s: float = 1.5) -> bool:
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
