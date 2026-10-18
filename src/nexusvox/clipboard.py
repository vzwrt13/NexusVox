"""Clipboard writes that stay out of the Windows clipboard history (Win+V).

Pasting goes through the clipboard: the text is put there, Ctrl+V is sent, and the
user's clipboard is put back. Through pyperclip every one of these writes is a new
entry in the clipboard history, so one dictation left its text plus the restored
clipboard there, and a polish several entries more. Writes made here carry the
formats Windows reads to keep content out of the history, the cloud clipboard and
clipboard monitors.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Formats documented for clipboard history: "CanIncludeInClipboardHistory" and
# "CanUploadToCloudClipboard" set to a DWORD 0 keep the content out of history and the
# cloud; "ExcludeClipboardContentFromMonitorProcessing" (any data) hides it from monitors.
_EXCLUDE_FORMATS = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
)
_OPEN_ATTEMPTS = 20

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL


class ClipboardError(Exception):
    """The clipboard could not be opened or written."""


def _set_data(fmt: int, data: bytes) -> None:
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise ClipboardError("GlobalAlloc failed")
    pointer = _kernel32.GlobalLock(handle)
    if not pointer:
        _kernel32.GlobalFree(handle)
        raise ClipboardError("GlobalLock failed")
    ctypes.memmove(pointer, data, len(data))
    _kernel32.GlobalUnlock(handle)
    if not _user32.SetClipboardData(fmt, handle):
        _kernel32.GlobalFree(handle)  # still ours: only a successful call hands it to the system
        raise ClipboardError(f"SetClipboardData failed (error {ctypes.get_last_error()})")


def set_text(text: str) -> None:
    """Put *text* on the clipboard, excluded from the clipboard history. Raises `ClipboardError`."""
    for _ in range(_OPEN_ATTEMPTS):  # another app may hold the clipboard for a moment
        if _user32.OpenClipboard(None):
            break
        time.sleep(0.01)
    else:
        raise ClipboardError(f"OpenClipboard failed (error {ctypes.get_last_error()})")
    try:
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        _set_data(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        for name in _EXCLUDE_FORMATS:
            _set_data(_user32.RegisterClipboardFormatW(name), (0).to_bytes(4, "little"))
    finally:
        _user32.CloseClipboard()
