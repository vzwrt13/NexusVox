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
import logging
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

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
CF_TEXT, CF_OEMTEXT, CF_DIB, CF_DIBV5 = 1, 7, 8, 17
CF_METAFILEPICT, CF_ENHMETAFILE = 3, 14
# Formats whose data is a GDI handle (bitmap, palette) or drawn by its owner, not global memory. Windows
# rebuilds a bitmap from the DIB formats, so only the enhanced metafile (a handle too) is copied, as its bits.
_HANDLE_FORMATS = frozenset({2, 3, 9, 0x80, 0x81, 0x82, 0x83, 0x8E})  # CF_BITMAP ... CF_DSPENHMETAFILE
# CF_PRIVATEFIRST..CF_GDIOBJLAST: data of any handle type its owner chose, so never assumed to be memory.
_OWNER_RANGE = (0x200, 0x3FF)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
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
_gdi32.GetEnhMetaFileBits.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p]
_gdi32.GetEnhMetaFileBits.restype = wintypes.UINT
_gdi32.SetEnhMetaFileBits.argtypes = [wintypes.UINT, ctypes.c_char_p]
_gdi32.SetEnhMetaFileBits.restype = wintypes.HANDLE
_gdi32.DeleteEnhMetaFile.argtypes = [wintypes.HANDLE]
_gdi32.DeleteEnhMetaFile.restype = wintypes.BOOL
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
    """Whether a snapshot can keep the data of *fmt*: plain global memory, or an enhanced metafile (as bits)."""
    return fmt not in _HANDLE_FORMATS and not _OWNER_RANGE[0] <= fmt <= _OWNER_RANGE[1]


def _without_synthesized(formats: list[int]) -> list[int]:
    """*formats* without those Windows rebuilds from another one on request, so they are not copied twice.

    CF_TEXT and CF_OEMTEXT come from CF_UNICODETEXT, CF_DIB and CF_DIBV5 from each other (the first listed is
    the owner's own: only that one is kept, a 4K screenshot is 30+ MB each), CF_METAFILEPICT from the
    enhanced metafile.
    """
    drop = set()
    if CF_UNICODETEXT in formats:
        drop |= {CF_TEXT, CF_OEMTEXT}
    drop |= set([f for f in formats if f in (CF_DIB, CF_DIBV5)][1:])
    if CF_ENHMETAFILE in formats:
        drop.add(CF_METAFILEPICT)
    return [f for f in formats if f not in drop]


def snapshot_formats() -> list[tuple[int, bytes]] | None:
    """What is on the clipboard, as (format, bytes) for every format that can be kept: text, rich text, an
    image, a file list, a metafile. Formats Windows rebuilds from another one are left out.

    An empty clipboard gives []. None when the clipboard cannot be opened. A format that cannot be read or kept
    (a bitmap handle, an owner-drawn or private format) is left out and logged.
    """
    try:
        _open()
    except ClipboardError:
        return None
    try:
        listed: list[int] = []
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            listed.append(fmt)
            fmt = _user32.EnumClipboardFormats(fmt)
        formats: list[tuple[int, bytes]] = []
        lost = []
        for fmt in _without_synthesized(listed):
            data = _read_format(fmt) if _is_copyable(fmt) else None
            if data is None:
                lost.append(fmt)
            else:
                formats.append((fmt, data))
        # Windows rebuilds a bitmap from a DIB, and a palette comes with its image: not lost when one was kept.
        lost = [f for f in lost if f not in (2, 9) or not formats]
        if lost:
            logger.warning("The clipboard holds formats that cannot be kept and will be gone: %s", lost)
        return formats
    finally:
        _user32.CloseClipboard()


def _read_format(fmt: int) -> bytes | None:
    handle = _user32.GetClipboardData(fmt)
    if not handle:
        return None
    if fmt == CF_ENHMETAFILE:
        size = _gdi32.GetEnhMetaFileBits(handle, 0, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        return buffer.raw if _gdi32.GetEnhMetaFileBits(handle, size, buffer) == size else None
    size = _kernel32.GlobalSize(handle)
    pointer = _kernel32.GlobalLock(handle) if size else None
    if not pointer:
        return None
    try:
        return ctypes.string_at(pointer, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def _set_metafile(data: bytes) -> None:
    handle = _gdi32.SetEnhMetaFileBits(len(data), data)
    if not handle:
        raise ClipboardError("SetEnhMetaFileBits failed")
    if not _user32.SetClipboardData(CF_ENHMETAFILE, handle):
        _gdi32.DeleteEnhMetaFile(handle)  # still ours: only a successful call hands it to the system
        raise ClipboardError(f"SetClipboardData failed (error {ctypes.get_last_error()})")


def restore_formats(formats: list[tuple[int, bytes]]) -> None:
    """Put back what `snapshot_formats` read, kept out of the clipboard history like `set_text`.

    A format the system refuses is skipped and logged, the others are still put back, and the history
    exclusion always goes with whatever was written. Raises `ClipboardError` when the clipboard cannot be
    opened or emptied. An empty list leaves the clipboard empty.
    """
    _open()
    try:
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        exclude_ids = {_user32.RegisterClipboardFormatW(name) for name in _EXCLUDE_FORMATS}
        written = 0
        for fmt, data in formats:
            if fmt in exclude_ids:
                continue
            try:
                _set_metafile(data) if fmt == CF_ENHMETAFILE else _set_data(fmt, data)
                written += 1
            except ClipboardError as exc:
                logger.warning("Could not put clipboard format %d back: %s", fmt, exc)
        if written:
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
