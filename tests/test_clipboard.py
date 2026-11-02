"""Clipboard writes kept out of the Windows clipboard history.

The Win32 layer is replaced by a fake, so the tests never touch the user's real clipboard
(or its history). Windows only: the module binds user32/kernel32 at import.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("the clipboard module calls Win32", allow_module_level=True)

from nexusvox import clipboard  # noqa: E402


class FakeWin32:
    """Stands in for user32 and kernel32; stores what would land on the clipboard."""

    def __init__(self, open_fails=0):
        self.open_fails = open_fails
        self.formats = {}  # format id -> bytes
        self.names = {}
        self.is_open = False
        self.blocks = {}

    # user32
    def OpenClipboard(self, hwnd):
        if self.open_fails:
            self.open_fails -= 1
            return 0
        self.is_open = True
        return 1

    def EmptyClipboard(self):
        self.formats.clear()
        return 1

    def CloseClipboard(self):
        self.is_open = False
        return 1

    def RegisterClipboardFormatW(self, name):
        return self.names.setdefault(name, 0xC000 + len(self.names))

    def SetClipboardData(self, fmt, handle):
        self.formats[fmt] = bytes(self.blocks.pop(handle))
        return handle

    # kernel32
    def GlobalAlloc(self, flags, size):
        handle = len(self.blocks) + 1
        self.blocks[handle] = bytearray(size)
        return handle

    def GlobalLock(self, handle):
        buffer = (clipboard.ctypes.c_char * len(self.blocks[handle])).from_buffer(self.blocks[handle])
        self._locked = buffer  # keep alive while memmove writes into it
        return clipboard.ctypes.addressof(buffer)

    def GlobalUnlock(self, handle):
        return 1

    def GlobalFree(self, handle):
        self.blocks.pop(handle, None)


@pytest.fixture
def win32(monkeypatch):
    fake = FakeWin32()
    monkeypatch.setattr(clipboard, "_user32", fake)
    monkeypatch.setattr(clipboard, "_kernel32", fake)
    monkeypatch.setattr(clipboard.time, "sleep", lambda s: None)
    return fake


def _text(fake):
    return fake.formats[clipboard.CF_UNICODETEXT].decode("utf-16-le").rstrip("\0")


def test_set_text_is_stored_and_excluded_from_history(win32):
    clipboard.set_text("Grüße aus NexusVox ✓")
    assert _text(win32) == "Grüße aus NexusVox ✓"
    for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
        assert win32.formats[win32.names[name]] == b"\x00\x00\x00\x00"
    assert "ExcludeClipboardContentFromMonitorProcessing" in win32.names
    assert not win32.is_open


def test_set_text_empty_leaves_no_text(win32):
    clipboard.set_text("")
    assert _text(win32) == ""


def test_set_text_retries_while_another_app_holds_the_clipboard(win32):
    win32.open_fails = 3
    clipboard.set_text("later")
    assert _text(win32) == "later"


def test_set_text_gives_up_and_raises(win32):
    win32.open_fails = 10_000
    with pytest.raises(clipboard.ClipboardError):
        clipboard.set_text("never")
