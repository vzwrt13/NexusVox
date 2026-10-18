"""Tests for clipboard writes kept out of the Windows clipboard history (real clipboard, Windows only)."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("the clipboard module calls Win32", allow_module_level=True)

import ctypes  # noqa: E402

import pyperclip  # noqa: E402

from nexusvox.clipboard import set_text  # noqa: E402


@pytest.fixture
def saved_clipboard():
    original = pyperclip.paste()
    yield
    set_text(original)


def _available(name: str) -> bool:
    user32 = ctypes.windll.user32
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    return bool(user32.IsClipboardFormatAvailable(user32.RegisterClipboardFormatW(name)))


@pytest.mark.usefixtures("saved_clipboard")
def test_set_text_is_readable_and_excluded_from_history():
    set_text("Grüße aus NexusVox ✓")
    assert pyperclip.paste() == "Grüße aus NexusVox ✓"
    assert _available("CanIncludeInClipboardHistory")
    assert _available("CanUploadToCloudClipboard")
    assert _available("ExcludeClipboardContentFromMonitorProcessing")


@pytest.mark.usefixtures("saved_clipboard")
def test_set_text_empty_leaves_no_text():
    set_text("")
    assert pyperclip.paste() == ""
