"""Tests for learning dictionary entries from edits after a paste."""

from __future__ import annotations

import threading

import pytest

from nexusvox import edit_watch
from nexusvox.dictionary import DictionaryEntry
from nexusvox.edit_watch import EditWatcher, extract_edit, learn

# --- extract_edit ---


def test_extract_edit_returns_the_corrected_span():
    before = "Hi Anna, I use nexus fox daily"
    after = "Hi Anna, I use NexusVox daily"
    assert extract_edit(before, "I use nexus fox daily", after) == "I use NexusVox daily"


def test_extract_edit_ignores_text_typed_after_the_dictation():
    before = "I use nexus fox daily"
    after = "I use NexusVox daily and love it"
    assert extract_edit(before, "I use nexus fox daily", after) == "I use NexusVox daily"


def test_extract_edit_ignores_text_typed_before_the_dictation():
    before = "I use nexus fox daily"
    after = "Hey, I use NexusVox daily"
    assert extract_edit(before, "I use nexus fox daily", after) == "I use NexusVox daily"


def test_extract_edit_german():
    before = "Ich teste Nexus Fox heute"
    after = "Ich teste NexusVox heute, danach Kaffee"
    assert extract_edit(before, "Ich teste Nexus Fox heute", after) == "Ich teste NexusVox heute"


def test_extract_edit_normalises_line_endings():
    before = "first line\r\nsecond nexus fox"
    after = "first line\r\nsecond NexusVox"
    assert extract_edit(before, "first line\nsecond nexus fox", after) == "first line\nsecond NexusVox"


def test_extract_edit_none_when_paste_not_in_field():
    assert extract_edit("something else", "I use nexus fox", "something else") is None


def test_extract_edit_none_when_deleted():
    assert extract_edit("Hi I use nexus fox", "I use nexus fox", "Hi ") is None


def test_extract_edit_none_when_rewritten():
    before = "I use nexus fox daily"
    assert extract_edit(before, before, "Completely different sentence here") is None


def test_extract_edit_in_a_long_field():
    filler = "lorem ipsum dolor sit amet " * 700
    before = filler + "I use nexus fox daily" + filler
    after = filler + "I use NexusVox daily" + filler
    assert extract_edit(before, "I use nexus fox daily", after) == "I use NexusVox daily"


# --- learn ---


def test_learn_suggests_the_changed_phrase():
    suggestions = learn("I use nexus fox daily", "I use nexus fox daily", "I use NexusVox daily", [])
    assert suggestions == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def test_learn_german():
    suggestions = learn("Wir treffen Herrn Maya", "Wir treffen Herrn Maya", "Wir treffen Herrn Meier", [])
    assert suggestions == [DictionaryEntry(wrong="Maya", right="Meier")]


def test_learn_nothing_when_unchanged():
    assert learn("I use NexusVox", "I use NexusVox", "I use NexusVox and more", []) == []


def test_learn_skips_known_entries():
    existing = [DictionaryEntry(wrong="nexus fox", right="NexusVox")]
    assert learn("I use nexus fox", "I use nexus fox", "I use NexusVox", existing) == []


def test_learn_skips_rewording():
    injected = "please send the report to the whole team by friday"
    after = "kindly send the summary to all of the team before monday"
    assert learn(injected, injected, after, []) == []


def test_learn_skips_long_replacements():
    injected = "we meet at the office tomorrow"
    after = "we meet at the big new shiny downtown office tomorrow"
    assert learn(injected, injected, after, []) == []


# --- EditWatcher ---


class FakeField:
    def __init__(self, texts: list[str | None]):
        self._texts = list(texts)

    def read(self) -> str | None:
        return self._texts.pop(0) if len(self._texts) > 1 else self._texts[0]


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch):
    monkeypatch.setattr(edit_watch, "_SETTLE_S", 0)
    monkeypatch.setattr(edit_watch, "_POLL_S", 0.01)


def _run_watch(field, injected: str, window_s: float = 0.1) -> list[DictionaryEntry]:
    got: list[DictionaryEntry] = []
    done = threading.Event()

    def on_suggestions(s):
        got.extend(s)
        done.set()

    watcher = EditWatcher(on_suggestions, lambda: [], field_factory=lambda: field)
    watcher.watch(injected, window_s)
    done.wait(2)
    watcher.stop()
    return got


def test_watcher_reports_the_last_readable_text():
    field = FakeField(["I use nexus fox", "I use nexus fo", "I use NexusVox"])
    assert _run_watch(field, "I use nexus fox") == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def test_watcher_keeps_last_text_when_field_disappears():
    field = FakeField(["I use nexus fox", "I use NexusVox", None])
    assert _run_watch(field, "I use nexus fox", window_s=5) == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def test_watcher_skips_without_a_readable_field():
    assert _run_watch(None, "I use nexus fox") == []


def test_watcher_next_watch_ends_the_running_one():
    fields = iter([FakeField(["I use nexus fox", "I use NexusVox"]), None])
    got: list[DictionaryEntry] = []
    done = threading.Event()

    def on_suggestions(s):
        got.extend(s)
        done.set()

    watcher = EditWatcher(on_suggestions, lambda: [], field_factory=lambda: next(fields))
    watcher.watch("I use nexus fox", 60)
    threading.Timer(0.1, watcher.watch, args=("next dictation", 60)).start()
    assert done.wait(2)
    watcher.stop()
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def test_watcher_survives_a_failing_field():
    class BrokenField:
        def __init__(self):
            self.calls = 0

        def read(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("gone")
            return "I use nexus fox"

    fields = iter([BrokenField(), FakeField(["I use nexus fox", "I use NexusVox"])])
    got: list[DictionaryEntry] = []
    done = threading.Event()

    def on_suggestions(s):
        got.extend(s)
        done.set()

    watcher = EditWatcher(on_suggestions, lambda: [], field_factory=lambda: next(fields))
    watcher.watch("I use nexus fox", 5)
    # Queued only after the first field has failed, so the failure is what ends that watch.
    threading.Timer(0.2, watcher.watch, args=("I use nexus fox", 0.1)).start()
    assert done.wait(2)
    watcher.stop()
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]
