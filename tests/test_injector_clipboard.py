"""The injector's clipboard handling and paste failures (Windows only: injector binds Win32 at import).

Everything Win32 is replaced, so nothing here touches the user's clipboard, sends a key or opens a window.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="injector binds Win32 at import")

IMAGE = [(8, b"dib-pixels"), (17, b"dibv5-pixels")]  # CF_DIB, CF_DIBV5: what an image on the clipboard holds


class FakeClipboard:
    """What the injector sees of the clipboard: the text pyperclip reads, the snapshot, and what is written."""

    def __init__(self, text="", formats=None, snapshot_works=True):
        self.formats = formats if formats is not None else ([(13, text.encode())] if text else [])
        self.text = text
        self.snapshot_works = snapshot_works
        self.writes = []  # ("text", value) or ("formats", value), in order

    def paste(self):
        return self.text

    def set_text(self, text):
        self.text = text
        self.formats = [(13, text.encode())]
        self.writes.append(("text", text))

    def snapshot_formats(self):
        return list(self.formats) if self.snapshot_works else None

    def restore_formats(self, formats):
        self.formats = list(formats)
        self.text = next((data.decode() for fmt, data in formats if fmt == 13), "")
        self.writes.append(("formats", list(formats)))


@pytest.fixture
def injector(monkeypatch):
    from nexusvox.dictation import injector

    monkeypatch.setattr(injector, "wait_for_physical_release", lambda timeout_s=1.5: True)
    monkeypatch.setattr(injector, "_held_modifiers", lambda: set())
    monkeypatch.setattr(injector.time, "sleep", lambda s: None)
    monkeypatch.setattr(injector, "_send_inputs", lambda inputs, name: True)
    return injector


def _use(monkeypatch, injector, clipboard):
    monkeypatch.setattr(injector.pyperclip, "paste", clipboard.paste)
    monkeypatch.setattr(injector, "set_text", clipboard.set_text)
    monkeypatch.setattr(injector, "snapshot_formats", clipboard.snapshot_formats)
    monkeypatch.setattr(injector, "restore_formats", clipboard.restore_formats)


# -- a clipboard that holds no text survives a dictation and a Polish ---------------------------------------


def test_an_image_on_the_clipboard_is_put_back_after_a_dictation(monkeypatch, injector):
    clipboard = FakeClipboard(text="", formats=IMAGE)
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.writes == [("text", "hello"), ("formats", IMAGE)]  # never an empty text written back
    assert clipboard.formats == IMAGE


def test_formatted_text_keeps_its_formatting(monkeypatch, injector):
    """Regression: only the plain text of a Word or web copy came back, its HTML, RTF and images were gone."""
    rich = [(13, b"my notes"), (0xD001, b"<b>my notes</b>"), (0xD002, b"rtf-data")]
    clipboard = FakeClipboard(text="my notes", formats=rich)
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.writes == [("text", "hello"), ("formats", rich)]
    assert clipboard.formats == rich


def test_a_text_clipboard_is_put_back(monkeypatch, injector):
    clipboard = FakeClipboard(text="my notes")
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.text == "my notes"


def test_an_empty_clipboard_is_left_empty(monkeypatch, injector):
    clipboard = FakeClipboard(text="", formats=[])
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.writes == [("text", "hello"), ("formats", [])]
    assert clipboard.formats == []


def test_a_clipboard_that_cannot_be_snapshotted_still_gets_its_text_back(monkeypatch, injector):
    """The snapshot cannot open the clipboard (a manager holds it): the text pyperclip reads is put back."""
    clipboard = FakeClipboard(text="my notes", snapshot_works=False)
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.writes == [("text", "hello"), ("text", "my notes")]


def test_a_clipboard_nothing_could_read_is_not_restored(monkeypatch, injector):
    clipboard = FakeClipboard(text="", snapshot_works=False)
    _use(monkeypatch, injector, clipboard)

    def broken():
        raise injector.pyperclip.PyperclipException("busy")

    monkeypatch.setattr(injector.pyperclip, "paste", broken)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.writes == [("text", "hello")]


def test_polish_copy_puts_an_image_clipboard_back(monkeypatch, injector):
    clipboard = FakeClipboard(text="", formats=IMAGE)
    _use(monkeypatch, injector, clipboard)

    def copy():  # Ctrl+C: the app replaces the emptied clipboard with the selection
        clipboard.text = "the selected sentence"
        return True

    monkeypatch.setattr(injector, "_send_ctrl_chord", lambda vk, scan, name: copy())
    assert injector.copy_selection(0, timeout_s=0.5) == "the selected sentence"
    assert clipboard.formats == IMAGE


def test_polish_without_a_selection_puts_an_image_clipboard_back(monkeypatch, injector):
    clipboard = FakeClipboard(text="", formats=IMAGE)
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_chord", lambda vk, scan, name: True)  # nothing is copied
    assert injector.copy_selection(0, timeout_s=0.1) == ""
    assert clipboard.formats == IMAGE


# -- a whole line copied without a selection is no selection ------------------------------------------------


@pytest.mark.parametrize("copied", ["the caret line\r\n", "the caret line\n", "\r\n", "  indented line  \n"])
def test_a_whole_line_copy_in_vs_code_counts_as_no_selection(monkeypatch, injector, copied):
    clipboard = FakeClipboard(text="old")
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_copies_whole_line_without_selection", lambda: True)

    def copy(vk, scan, name):
        clipboard.text = copied
        return True

    monkeypatch.setattr(injector, "_send_ctrl_chord", copy)
    assert injector.copy_selection(0, timeout_s=0.5) == ""
    assert clipboard.text == "old"  # and the clipboard is back as it was


@pytest.mark.parametrize(
    "copied",
    ["one sentence", "two lines\r\nof text", "two lines\r\nof text\r\n", "a paragraph.\n\nAnother one.\n"],
)
def test_a_real_selection_is_returned(monkeypatch, injector, copied):
    clipboard = FakeClipboard(text="old")
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_copies_whole_line_without_selection", lambda: True)

    def copy(vk, scan, name):
        clipboard.text = copied
        return True

    monkeypatch.setattr(injector, "_send_ctrl_chord", copy)
    assert injector.copy_selection(0, timeout_s=0.5) == copied


@pytest.mark.parametrize("copied", ["some line\n", "some line\r\n"])
def test_a_one_line_selection_with_its_break_is_kept_outside_vs_code(monkeypatch, injector, copied):
    """Polish keeps a selected line's newline on purpose: in Slack or Obsidian it is a selection."""
    clipboard = FakeClipboard(text="old")
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_copies_whole_line_without_selection", lambda: False)

    def copy(vk, scan, name):
        clipboard.text = copied
        return True

    monkeypatch.setattr(injector, "_send_ctrl_chord", copy)
    assert injector.copy_selection(0, timeout_s=0.5) == copied


@pytest.mark.parametrize(
    ("process", "expected"),
    [
        ("Code.exe", True),
        ("Code - Insiders.exe", True),
        ("VSCodium.exe", True),
        ("Cursor.exe", True),
        ("slack.exe", False),
        ("Obsidian.exe", False),
        ("", False),
    ],
)
def test_which_apps_copy_a_line_without_a_selection(monkeypatch, injector, process, expected):
    from nexusvox.system import window_manager

    monkeypatch.setattr(window_manager, "foreground_app", lambda: (process, "title"))
    assert injector._copies_whole_line_without_selection() is expected


def test_an_unreadable_foreground_app_is_not_an_editor(monkeypatch, injector):
    from nexusvox.system import window_manager

    def broken():
        raise OSError("no window")

    monkeypatch.setattr(window_manager, "foreground_app", broken)
    assert injector._copies_whole_line_without_selection() is False


# -- the paste fallback is not a way around UIPI -----------------------------------------------------------


def test_a_dictation_nothing_could_paste_raises_and_still_restores_the_clipboard(monkeypatch, injector):
    from nexusvox.dictation.clipboard import ClipboardError

    clipboard = FakeClipboard(text="my notes")
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: False)  # SendInput blocked (elevated target)
    monkeypatch.setattr(injector, "_paste_via_message", lambda: False)  # and so is WM_PASTE
    sent = []
    monkeypatch.setattr(injector, "_send_enter", lambda: sent.append("enter") or True)
    with pytest.raises(ClipboardError):  # the type every caller already answers with the error cue
        injector.inject_text("hello", 0, press_enter=True)
    assert sent == []  # no Enter into a field the text never reached
    assert clipboard.text == "my notes"


def test_wm_paste_rescues_a_blocked_send_input(monkeypatch, injector):
    clipboard = FakeClipboard(text="my notes")
    _use(monkeypatch, injector, clipboard)
    monkeypatch.setattr(injector, "_send_ctrl_v", lambda: False)
    monkeypatch.setattr(injector, "_paste_via_message", lambda: True)
    injector.inject_text("hello", 0)
    assert clipboard.text == "my notes"


def test_a_failed_wm_paste_is_logged_as_blocked_not_as_bypassing_uipi(monkeypatch, injector, caplog):
    class FakeUser32:
        def GetForegroundWindow(self):
            return 0x1234

        def GetWindowThreadProcessId(self, hwnd, pid):
            return 0

        def PostMessageW(self, hwnd, msg, wparam, lparam):
            return 0

    monkeypatch.setattr(injector, "user32", FakeUser32())
    with caplog.at_level("ERROR", logger="nexusvox.injector"):
        assert injector._paste_via_message() is False
    assert "failed" in caplog.text and "elevated" in caplog.text


# -- one SendInput wrapper ---------------------------------------------------------------------------------


def test_ctrl_chord_and_enter_go_through_send_inputs(monkeypatch, injector):
    calls = []
    monkeypatch.setattr(injector, "_send_inputs", lambda inputs, name: calls.append((len(inputs), name)) or True)
    assert injector._send_ctrl_v() is True
    assert injector._send_enter() is True
    assert calls == [(4, "Ctrl+V"), (2, "Enter")]


def test_a_short_send_input_count_is_a_failure(monkeypatch, injector, caplog):
    class ShortUser32:
        def SendInput(self, count, array, size):
            return count - 1

    monkeypatch.undo()  # the fixture's stub of _send_inputs must not hide the real one
    from nexusvox.dictation import injector as real

    monkeypatch.setattr(real, "user32", ShortUser32())
    with caplog.at_level("ERROR", logger="nexusvox.injector"):
        assert real._send_enter() is False
        assert real._send_ctrl_v() is False
    assert caplog.text.count("sent 1/2") == 1 and caplog.text.count("sent 3/4") == 1
