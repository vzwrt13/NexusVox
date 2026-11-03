"""Clipboard writes kept out of the Windows clipboard history.

Most tests run the real module against a fake user32/kernel32, so they never touch the user's clipboard
(or its history) and run on every platform. The one test that checks the real ctypes bindings drives the
real clipboard: it is marked ``desktop`` and skipped unless run with ``-m desktop`` on Windows.
"""

import ctypes
import importlib.util
import sys
from pathlib import Path

import pytest

import nexusvox

CLIPBOARD_SOURCE = Path(nexusvox.__file__).with_name("clipboard.py")


class _Function:
    """A Win32 function the module sets ``argtypes``/``restype`` on and then calls."""

    def __init__(self, implementation):
        self._implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._implementation(*args)


class FakeWin32:
    """Stands in for user32 and kernel32 with the same rules: most calls need the clipboard to be open."""

    def __init__(self):
        self.open_fails = 0
        self.fail = set()  # names of calls that return 0
        self.is_open = False
        self.close_calls = 0
        self.formats = {}  # format id -> bytes
        self.names = {}
        self.blocks = {}  # allocated, not yet handed to the clipboard
        self._next_handle = 0
        self._buffers = []

    def __getattr__(self, name):
        function = _Function(getattr(self, "_" + name))
        setattr(self, name, function)
        return function

    # user32
    def _OpenClipboard(self, hwnd):
        if self.open_fails:
            self.open_fails -= 1
            return 0
        self.is_open = True
        return 1

    def _EmptyClipboard(self):
        assert self.is_open, "EmptyClipboard needs the clipboard to be open"
        if "EmptyClipboard" in self.fail:
            return 0
        self.formats.clear()
        return 1

    def _CloseClipboard(self):
        assert self.is_open, "CloseClipboard without OpenClipboard"
        self.is_open = False
        self.close_calls += 1
        return 1

    def _RegisterClipboardFormatW(self, name):
        return self.names.setdefault(name, 0xC000 + len(self.names))

    def _SetClipboardData(self, fmt, handle):
        assert self.is_open, "SetClipboardData needs the clipboard to be open"
        if "SetClipboardData" in self.fail:
            return 0
        self.formats[fmt] = bytes(self.blocks.pop(handle))
        return handle

    # kernel32
    def _GlobalAlloc(self, flags, size):
        if "GlobalAlloc" in self.fail:
            return 0
        self._next_handle += 1  # never reused, so a live block is never overwritten
        self.blocks[self._next_handle] = bytearray(size)
        return self._next_handle

    def _GlobalLock(self, handle):
        if "GlobalLock" in self.fail:
            return 0
        buffer = (ctypes.c_char * len(self.blocks[handle])).from_buffer(self.blocks[handle])
        self._buffers.append(buffer)  # alive while memmove writes into it
        return ctypes.addressof(buffer)

    def _GlobalUnlock(self, handle):
        return 1

    def _GlobalFree(self, handle):
        self.blocks.pop(handle, None)
        return 0


@pytest.fixture
def win32(monkeypatch):
    """The clipboard module, loaded fresh with the fake in place of the Win32 libraries."""
    fake = FakeWin32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: fake, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    spec = importlib.util.spec_from_file_location("nexusvox_clipboard_under_test", CLIPBOARD_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    fake.module = module
    return fake


def _raw_text(fake):
    return fake.formats[fake.module.CF_UNICODETEXT]


def _text(fake):
    return _raw_text(fake).decode("utf-16-le").removesuffix("\0")


def test_set_text_is_stored_with_its_terminator(win32):
    win32.module.set_text("Grüße aus NexusVox ✓")
    assert _text(win32) == "Grüße aus NexusVox ✓"
    assert _raw_text(win32).endswith(b"\x00\x00")
    assert not win32.is_open


def test_set_text_empty_is_just_the_terminator(win32):
    win32.module.set_text("")
    assert _raw_text(win32) == b"\x00\x00"


def test_set_text_sets_the_history_and_monitor_exclusion_formats(win32):
    win32.module.set_text("secret")
    for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
        assert win32.formats[win32.names[name]] == b"\x00\x00\x00\x00"
    assert win32.names["ExcludeClipboardContentFromMonitorProcessing"] in win32.formats


def test_set_text_retries_while_another_app_holds_the_clipboard(win32):
    win32.open_fails = 3
    win32.module.set_text("later")
    assert _text(win32) == "later"


def test_set_text_gives_up_and_raises(win32):
    win32.open_fails = 10_000
    with pytest.raises(win32.module.ClipboardError):
        win32.module.set_text("never")


@pytest.mark.parametrize("failing", ["EmptyClipboard", "GlobalAlloc", "GlobalLock", "SetClipboardData"])
def test_a_failed_write_raises_frees_memory_and_closes_the_clipboard(win32, failing):
    win32.fail.add(failing)
    with pytest.raises(win32.module.ClipboardError):
        win32.module.set_text("x")
    assert not win32.is_open and win32.close_calls == 1  # other apps can use the clipboard again
    assert win32.blocks == {}  # nothing allocated is left behind


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="the real clipboard is a Windows one")
def test_the_real_clipboard_round_trip():
    """The only test that exercises the module's real ctypes bindings (argtypes/restype)."""
    import pyperclip

    from nexusvox.clipboard import set_text

    original = pyperclip.paste()
    try:
        set_text("Grüße aus NexusVox ✓")
        assert pyperclip.paste() == "Grüße aus NexusVox ✓"
        user32 = ctypes.windll.user32
        user32.RegisterClipboardFormatW.restype = ctypes.c_uint
        assert user32.IsClipboardFormatAvailable(user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory"))
        set_text("")
        assert pyperclip.paste() == ""
    finally:
        set_text(original)
