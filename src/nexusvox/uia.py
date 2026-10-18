"""What the focused control holds, read through UI Automation without sending a key.

Polish needs the user's selection. Asking the control is safer than pressing Ctrl+C:
in a terminal Ctrl+C interrupts the running program, and some editors copy the whole
line when nothing is selected. `read_focus` answers both questions at once - is the
focus a terminal, and what is selected - and says when the control cannot tell, so
the caller may fall back to the clipboard.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TEXT_PATTERN_ID = 10014
_SELECTION_CHARS = 100_000

# Top-level windows of terminal programs (Win32 class names).
_TERMINAL_WINDOWS = frozenset(
    {
        "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
        "ConsoleWindowClass",  # classic console host
        "mintty",  # Git Bash, Cygwin, MSYS2
        "VirtualConsoleClass",  # ConEmu, Cmder
        "PuTTY",
    }
)
# Focused UI Automation elements of terminals embedded in other apps.
_TERMINAL_ELEMENTS = frozenset(
    {
        "xterm-helper-textarea",  # xterm.js: VS Code, Orca, Hyper, other Electron terminals
        "TermControl",  # Windows Terminal's terminal surface
    }
)


@dataclass(frozen=True)
class Focus:
    terminal: bool
    selection: str | None  # None: the control does not report its selection


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def read_focus() -> Focus:
    """The focused control: a terminal or not, and its selected text if it reports one.

    Runs on the calling thread; the caller initialises COM there.
    """
    if _window_class(ctypes.windll.user32.GetForegroundWindow()) in _TERMINAL_WINDOWS:
        return Focus(terminal=True, selection=None)
    try:
        import comtypes.client

        uia = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        element = automation.GetFocusedElement()
        if element is None:
            return Focus(terminal=False, selection=None)
        if element.CurrentClassName in _TERMINAL_ELEMENTS:
            return Focus(terminal=True, selection=None)
        pattern = element.GetCurrentPattern(_TEXT_PATTERN_ID)
        if not pattern:
            return Focus(terminal=False, selection=None)
        ranges = pattern.QueryInterface(uia.IUIAutomationTextPattern).GetSelection()
        text = "".join(ranges.GetElement(i).GetText(_SELECTION_CHARS) for i in range(ranges.Length))
    except Exception:
        logger.debug("UI Automation could not read the focused control", exc_info=True)
        return Focus(terminal=False, selection=None)
    return Focus(terminal=False, selection=text)
