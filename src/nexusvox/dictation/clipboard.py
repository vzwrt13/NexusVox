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
# ~500 ms, as long as pyperclip waited: clipboard managers and Office hold it for a moment.
_OPEN_ATTEMPTS = 50
# Formats whose data is a GDI handle (bitmap, palette, metafiles) or drawn by its owner, not global memory.
_HANDLE_FORMATS = frozenset({2, 3, 9, 14, 0x80, 0x81, 0x82, 0x83, 0x8E})  # CF_BITMAP ... CF_DSPENHMETAFILE
_GDI_RANGE = (0x300, 0x3FF)  # CF_GDIOBJFIRST..CF_GDIOBJLAST

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t
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


def _open() -> None:
    for _ in range(_OPEN_ATTEMPTS):  # another app may hold the clipboard for a moment
        if _user32.OpenClipboard(None):
            return
        time.sleep(0.01)
    raise ClipboardError(f"OpenClipboard failed (error {ctypes.get_last_error()})")


def _is_copyable(fmt: int) -> bool:
    """Whether the data of *fmt* is plain global memory, which is all a snapshot can keep.

    Bitmaps, palettes and metafiles are GDI handles, and owner-display formats are drawn by the app that
    owns them. Windows rebuilds a bitmap from the DIB formats, which are memory.
    """
    return fmt not in _HANDLE_FORMATS and not _GDI_RANGE[0] <= fmt <= _GDI_RANGE[1]


def snapshot_formats() -> list[tuple[int, bytes]] | None:
    """Every format on the clipboard that is plain memory, as (format, bytes): an image, a file list, rich text.

    An empty clipboard gives []. None when the clipboard cannot be opened or read. Used to put back a
    clipboard that `pyperclip.paste()` reads as "" because it holds no text.
    """
    try:
        _open()
    except ClipboardError:
        return None
    try:
        formats: list[tuple[int, bytes]] = []
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            if _is_copyable(fmt):
                data = _read_format(fmt)
                if data is not None:
                    formats.append((fmt, data))
            fmt = _user32.EnumClipboardFormats(fmt)
        return formats
    finally:
        _user32.CloseClipboard()


def _read_format(fmt: int) -> bytes | None:
    handle = _user32.GetClipboardData(fmt)
    if not handle:
        return None
    size = _kernel32.GlobalSize(handle)
    pointer = _kernel32.GlobalLock(handle) if size else None
    if not pointer:
        return None
    try:
        return ctypes.string_at(pointer, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def restore_formats(formats: list[tuple[int, bytes]]) -> None:
    """Put back what `snapshot_formats` read, kept out of the clipboard history like `set_text`.

    Raises `ClipboardError`. An empty list leaves the clipboard empty.
    """
    _open()
    try:
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        exclude_ids = {_user32.RegisterClipboardFormatW(name) for name in _EXCLUDE_FORMATS}
        for fmt, data in formats:
            if fmt not in exclude_ids:
                _set_data(fmt, data)
        if formats:
            _set_exclusions()
    finally:
        _user32.CloseClipboard()


def _set_exclusions() -> None:
    for name in _EXCLUDE_FORMATS:
        _set_data(_user32.RegisterClipboardFormatW(name), (0).to_bytes(4, "little"))


def set_text(text: str) -> None:
    """Put *text* on the clipboard, excluded from the clipboard history. Raises `ClipboardError`."""
    _open()
    try:
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        _set_data(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        _set_exclusions()
    finally:
        _user32.CloseClipboard()
