"""Tests for learning dictionary entries from edits after a paste."""

from __future__ import annotations

import threading
import time

import pytest

from nexusvox import edit_watch
from nexusvox.dictionary import DictionaryEntry
from nexusvox.edit_watch import OUTCOMES, EditWatcher, FieldSkipped, extract_edit, learn

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


class DyingField(FakeField):
    """Reads its texts, then reports the element dead (a browser rebuilt the field)."""

    def read(self) -> str | None:
        return self._texts.pop(0) if self._texts else None


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


def test_learn_skips_a_language_mix_up():
    # Spoken German "jetzt aber", transcribed as English: a dictionary entry would rewrite real English.
    assert learn("Okay, it's our test.", "Okay, it's our test.", "Okay, jetzt aber Test.", []) == []


def test_learn_skips_a_language_mix_up_the_other_way():
    assert learn("Ich mache das mein Weg", "Ich mache das mein Weg", "Ich mache das my way", []) == []


@pytest.mark.parametrize(
    ("injected", "after", "expected"),
    [
        ("Wir treffen Herrn Maya", "Wir treffen Herrn Meier", DictionaryEntry(wrong="Maya", right="Meier")),
        ("I use nexus fox daily", "I use NexusVox daily", DictionaryEntry(wrong="nexus fox", right="NexusVox")),
        ("Morgen ist das Meeting", "Morgen ist das Treffen", DictionaryEntry(wrong="Meeting", right="Treffen")),
        ("Open cloud code now", "Open Claude Code now", DictionaryEntry(wrong="cloud code", right="Claude Code")),
    ],
)
def test_learn_keeps_same_language_fixes(injected, after, expected):
    assert learn(injected, injected, after, []) == [expected]


# --- whitespace, spoken words, outcomes ---


def test_extract_edit_finds_a_rewrapped_paste():
    before = "Notes: I use nexus\n  fox daily"
    assert extract_edit(before, "I use nexus fox", "Notes: I use NexusVox daily") == "I use NexusVox"


def test_learn_from_a_rewrapped_paste():
    before = "I use nexus\nfox daily"
    expected = [DictionaryEntry(wrong="nexus fox", right="NexusVox")]
    assert learn(before, "I use nexus fox", "I use NexusVox daily", []) == expected


def test_learn_nothing_when_only_the_wrapping_changed():
    assert learn("I use nexus fox", "I use nexus fox", "I use nexus\nfox", []) == []


@pytest.mark.parametrize(
    ("typed", "after", "spoken"),
    [
        ("I have 25 apples", "I have 26 apples", "I have twenty five apples"),
        ("ich habe 25 Äpfel", "ich habe 26 Äpfel", "ich habe fünfundzwanzig Äpfel"),
    ],
)
def test_learn_drops_a_fix_of_what_voice_commands_typed(typed, after, spoken):
    assert learn(typed, typed, after, [], spoken) == []


@pytest.mark.parametrize(
    ("typed", "after", "spoken"),
    [
        ("Hi\nI use nexus fox", "Hi\nI use NexusVox", "Hi new line I use nexus fox"),
        ("Hallo\nich nutze nexus fox", "Hallo\nich nutze NexusVox", "Hallo neue Zeile ich nutze nexus fox"),
    ],
)
def test_learn_keeps_a_fix_of_spoken_words_next_to_voice_commands(typed, after, spoken):
    assert learn(typed, typed, after, [], spoken) == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def _outcome(field_factory, injected: str = "I use nexus fox") -> dict:
    watcher = EditWatcher(lambda s: None, lambda: [], field_factory=field_factory)
    watcher.watch(injected, 0.05)
    deadline = time.monotonic() + 2
    while not watcher.status()["counts"] and time.monotonic() < deadline:
        time.sleep(0.01)
    watcher.stop()
    return watcher.status()


@pytest.fixture
def short_paste_wait(monkeypatch):
    monkeypatch.setattr(edit_watch, "_PASTE_WAIT_S", 0.05)


def test_watcher_states_why_it_skipped_a_terminal():
    def terminal():
        raise FieldSkipped("terminal")

    status = _outcome(terminal)
    assert status["counts"] == {"terminal": 1}
    assert status["last"]["message"] == OUTCOMES["terminal"]


@pytest.mark.usefixtures("short_paste_wait")
@pytest.mark.parametrize(
    ("texts", "outcome"),
    [
        ([None], "unreadable"),
        (["something else"], "not_found"),
        (["x" * (edit_watch.MAX_FIELD_CHARS + 1)], "too_long"),
        (["I use nexus fox"], "unchanged"),
        (["I use nexus fox", "I use NexusVox"], "suggested"),
    ],
)
def test_watcher_counts_each_outcome(texts, outcome):
    field = FakeField(texts)
    assert _outcome(lambda: field)["counts"] == {outcome: 1}


def test_watcher_counts_a_missing_field():
    assert _outcome(lambda: None)["counts"] == {"no_field": 1}


def test_skip_is_counted_without_a_watch():
    watcher = EditWatcher(lambda s: None, lambda: [])
    watcher.skip("processed")
    assert watcher.status()["counts"] == {"processed": 1}
    assert watcher.status()["last"]["outcome"] == "processed"


def test_extract_edit_finds_the_last_of_overlapping_occurrences():
    # finditer would stop at the first "ha ha"; the paste is the one at the end.
    assert extract_edit("ha ha ha", "ha ha", "ha ha hu") == "ha hu"


# --- why nothing was learned ---


@pytest.mark.parametrize(
    ("before", "injected", "after", "outcome"),
    [
        ("a nexus fox b", "nexus fox", "a nexus fox b", "unchanged"),
        ("a nexus fox b", "nexus fox", "a nexus\u00a0fox  b", "unchanged"),
        ("a nexus fox b", "nexus fox", "a nexus\nfox b", "too_small"),
        ("unrelated text", "nexus fox", "unrelated text", "not_found"),
        ("I use nexus\nfox", "I use nexus fox", "I use nexus fox", "too_small"),
        ("I use nexus fox", "I use nexus fox", "Completely different sentence here", "rewritten"),
        ("Hi I use nexus fox", "I use nexus fox", "Hi ", "rewritten"),
        (
            "please send the report to the whole team",
            "please send the report to the whole team",
            "please send the summary to all the team",
            "too_many_changes",
        ),
        ("I use nexus fox", "I use nexus fox", "I really use nexus fox", "no_replacement"),
        (
            "Okay so then it's our test today for sure",
            "Okay so then it's our test today for sure",
            "Okay so then jetzt aber test today for sure",
            "no_replacement",
        ),
        ("Ich nutze Nexus Fox", "Ich nutze Nexus Fox", "Ich nutze NexusVox", "suggested"),
        ("Ich nutze Nexus Fox", "Ich nutze Nexus Fox", "Ich nutze Nexus Fox heute", "unchanged"),
    ],
)
def test_judge_names_the_reason(before, injected, after, outcome):
    verdict = edit_watch.judge(before, injected, after, [])
    assert verdict.outcome == outcome
    assert bool(verdict.suggestions) == (outcome == "suggested")
    assert verdict.outcome in OUTCOMES


def test_judge_rewritten_says_how_much_is_left():
    verdict = edit_watch.judge("I use nexus fox", "I use nexus fox", "Completely different sentence here", [])
    assert "needed" in verdict.detail
    gone = edit_watch.judge("Hi I use nexus fox", "I use nexus fox", "Hi ", [])
    assert gone.detail == "the dictation is gone from the field"


def test_judge_too_many_changes_gives_the_numbers():
    text = "please send the report to the whole team"
    detail = edit_watch.judge(text, text, "please send the summary to all the team", []).detail
    assert f"at most {edit_watch.MAX_CHANGES}" in detail
    assert "words kept" in detail


def test_judge_says_when_the_dictation_was_never_there():
    verdict = edit_watch.judge("unrelated text", "nexus fox", "unrelated text", [])
    assert "never" not in verdict.detail  # worded as a fact about the starting text
    assert "not in the text the watch started from" in verdict.detail


@pytest.mark.parametrize(
    ("before", "injected", "after"),
    [
        # Inline autocomplete appends a completion to the last word: one word of three survives.
        ("open nexus fox", "open nexus fox", "open NexusVox.de/pricing"),
        # The address bar swaps the typed words for a search URL after Enter.
        ("nexus fox pricing", "nexus fox pricing", "https://www.google.com/search?q=nexus+fox+pricing"),
    ],
)
def test_address_bar_rewrites_are_reported_not_silent(before, injected, after):
    verdict = edit_watch.judge(before, injected, after, [])
    assert verdict.outcome == "too_many_changes"
    assert "words kept" in verdict.detail


def test_text_autocompleted_after_the_dictation_is_not_part_of_the_edit():
    verdict = edit_watch.judge("open nexus fox now", "open nexus fox now", "open NexusVox now/pricing", [])
    assert verdict.outcome == "suggested"


def _log_of(field_factory, caplog, injected="I use nexus fox", window_s=0.05, next_dictation=None):
    watcher = EditWatcher(lambda s: None, lambda: [], field_factory=field_factory)
    with caplog.at_level("INFO", logger=edit_watch.logger.name):
        watcher.watch(injected, window_s)
        if next_dictation:
            time.sleep(0.1)
            watcher.watch(next_dictation, window_s)
        deadline = time.monotonic() + 2
        while not watcher.status()["counts"] and time.monotonic() < deadline:
            time.sleep(0.01)
        watcher.stop()
    return watcher, [r.getMessage() for r in caplog.records if r.name == edit_watch.logger.name]


def test_log_names_the_reason_and_that_the_window_ran_out(caplog):
    field = FakeField(["I use nexus fox", "I really use nexus fox"])
    _, lines = _log_of(lambda: field, caplog, window_s=0.1)
    assert any(OUTCOMES["no_replacement"] in line and "time window over" in line for line in lines)


def test_log_names_the_next_dictation_as_the_end(caplog):
    fields = iter([FakeField(["I use nexus fox"]), None])
    _, lines = _log_of(lambda: next(fields), caplog, window_s=30, next_dictation="next dictation")
    assert any(OUTCOMES["unchanged"] in line and "the next dictation arrived" in line for line in lines)


def test_a_field_that_dies_before_an_edit_is_not_reported_as_unchanged(caplog):
    fields = iter([DyingField(["I use nexus fox"])])
    watcher, lines = _log_of(lambda: next(fields, None), caplog, window_s=5)
    assert watcher.status()["counts"] == {"lost_field": 1}
    assert any("could not be read any more" in line for line in lines)


def test_a_failed_read_keeps_its_error_and_logs_it_at_debug(caplog):
    class Element:
        def GetCurrentPattern(self, _id):
            raise OSError("element is gone")

    field = edit_watch.UiaTextField(None, None, Element())
    with caplog.at_level("DEBUG", logger=edit_watch.logger.name):
        assert field.read() is None
    assert str(field.last_error) == "element is gone"
    assert [r.levelname for r in caplog.records if "element is gone" in r.getMessage()] == ["DEBUG"]


def test_a_field_rebuilt_on_every_poll_is_announced_once_per_watch(caplog):
    def rebuilt():
        return DyingField(["I use nexus fox", "I use nexus fox"])

    with caplog.at_level("INFO", logger=edit_watch.logger.name):
        watcher, _ = _watch_with(rebuilt, window_s=0.3)
    lines = [r.getMessage() for r in caplog.records if r.name == edit_watch.logger.name]
    assert sum("reading the field failed" in line for line in lines) == 1
    assert len(lines) <= 2  # that line and the outcome


def test_a_dead_field_after_a_rewrap_is_lost_not_too_small(caplog):
    fields = iter([DyingField(["I use nexus" + chr(10) + "fox", "I use nexus fox"])])
    watcher, _ = _log_of(lambda: next(fields, None), caplog, injected="I use nexus fox", window_s=5)
    assert watcher.status()["counts"] == {"lost_field": 1}


# --- a rebuilt field is fetched again ---


def _watch_with(factory, window_s=0.3, injected="I use nexus fox"):
    got: list[DictionaryEntry] = []
    done = threading.Event()

    def on_suggestions(s):
        got.extend(s)
        done.set()

    watcher = EditWatcher(on_suggestions, lambda: [], field_factory=factory)
    watcher.watch(injected, window_s)
    done.wait(2)
    deadline = time.monotonic() + 2
    while not watcher.status()["counts"] and time.monotonic() < deadline:
        time.sleep(0.01)
    watcher.stop()
    return watcher, got


def test_a_rebuilt_field_is_fetched_again_and_its_edit_counts():
    fields = iter([DyingField(["I use nexus fox"]), FakeField(["I use NexusVox"])])
    watcher, got = _watch_with(lambda: next(fields))
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]
    assert watcher.status()["counts"] == {"suggested": 1}


def test_the_refetch_retries_while_the_new_element_is_not_ready():
    fields = iter([DyingField(["I use nexus fox"]), None, DyingField([]), FakeField(["I use NexusVox"])])
    _, got = _watch_with(lambda: next(fields))
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def test_a_refetched_element_that_is_another_field_is_not_diffed():
    # Focus moved on to a page: the last good read (the fixed dictation) must still be the one that counts.
    page = FakeField(["Welcome to our shop. Nothing to do with the dictation at all."])
    fields = iter([DyingField(["I use nexus fox", "I use NexusVox"]), page, page, page])
    _, got = _watch_with(lambda: next(fields))
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


def test_the_refetch_gives_up_after_a_few_tries():
    calls = []

    def factory():
        calls.append(1)
        return DyingField(["I use nexus fox"]) if len(calls) == 1 else None

    watcher, _ = _watch_with(factory)
    assert watcher.status()["counts"] == {"lost_field": 1}
    assert len(calls) == 1 + edit_watch._REFETCH_TRIES


def test_a_refetched_password_field_ends_the_watch():
    calls = []

    def factory():
        calls.append(1)
        if len(calls) == 1:
            return DyingField(["I use nexus fox"])
        raise FieldSkipped("password")

    watcher, _ = _watch_with(factory)
    assert watcher.status()["counts"] == {"lost_field": 1}
    assert len(calls) == 2


def test_a_field_rebuilt_while_waiting_for_the_paste_is_fetched_again():
    # The address bar still shows the old text, then is rebuilt: the first read never saw the paste.
    fields = iter([DyingField(["old address"]), FakeField(["I use nexus fox", "I use NexusVox"])])
    watcher, got = _watch_with(lambda: next(fields))
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]
    assert watcher.status()["counts"] == {"suggested": 1}


def test_a_refetched_first_element_without_the_dictation_is_not_announced(caplog):
    fields = iter([DyingField([]), FakeField(["something else"])])
    with caplog.at_level("DEBUG", logger=edit_watch.logger.name):
        watcher, _ = _watch_with(lambda: next(fields, None))
    assert watcher.status()["counts"] == {"unreadable": 1}
    assert not any("fetched the focused element again" in r.getMessage() for r in caplog.records)


def test_a_refetched_field_with_one_fixed_word_is_still_ours():
    injected = "please open nexus fox now"
    fields = iter([DyingField([injected]), FakeField(["please open NexusVox now"])])
    watcher, got = _watch_with(lambda: next(fields), injected=injected)
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]


@pytest.mark.usefixtures("short_paste_wait")
def test_a_first_read_that_fails_is_retried_on_the_focused_element():
    fields = iter([FakeField([None]), FakeField(["I use nexus fox", "I use NexusVox"])])
    watcher, got = _watch_with(lambda: next(fields))
    assert got == [DictionaryEntry(wrong="nexus fox", right="NexusVox")]
    assert watcher.status()["counts"] == {"suggested": 1}


def test_status_lists_which_outcomes_are_watched():
    status = EditWatcher(lambda s: None, lambda: []).status()
    assert set(status["watched"]) <= set(OUTCOMES)
    assert "suggested" in status["watched"]
    assert "terminal" not in status["watched"]


# --- review round 3 ---


def _small_page_with_the_words():
    return FakeField(["Results for fox: nexus tips and github repos, nothing else here"])


def test_a_page_with_the_dictated_words_is_not_our_field():
    # Enter in the address bar: the bar dies, the focused results page holds all three words.
    fields = iter([DyingField(["nexus fox github"]), _small_page_with_the_words()])
    watcher, got = _watch_with(lambda: next(fields, None), injected="nexus fox github")
    assert got == []
    assert watcher.status()["counts"] == {"lost_field": 1}


def test_a_long_page_that_quotes_the_dictation_is_not_our_field():
    page = FakeField(["Search results. " * 40 + "nexus fox github" + " More results." * 40])
    fields = iter([DyingField(["nexus fox github"]), page])
    watcher, _ = _watch_with(lambda: next(fields, None), injected="nexus fox github")
    assert watcher.status()["counts"] == {"lost_field": 1}


def test_the_first_read_refetch_does_not_wait_for_the_paste_in_every_try(monkeypatch):
    monkeypatch.setattr(edit_watch, "_PASTE_WAIT_S", 2.0)
    monkeypatch.setattr(edit_watch, "_POLL_S", 1.0)
    fields = iter([DyingField([])] + [FakeField(["something else"])] * 5)
    started = time.monotonic()
    status = _outcome(lambda: next(fields, None))
    # Three tries of 0.2 s, not three waits of 2 s.
    assert time.monotonic() - started < 1.8
    assert status["counts"] == {"unreadable": 1}


def test_a_too_long_field_that_then_dies_is_still_reported_as_too_long():
    fields = iter([DyingField(["x" * (edit_watch.MAX_FIELD_CHARS + 1)])])
    watcher, _ = _watch_with(lambda: next(fields, None))
    assert watcher.status()["counts"] == {"too_long": 1}


def test_a_paste_that_never_showed_before_the_field_died_is_reported_as_not_found():
    fields = iter([DyingField(["old address"])])
    watcher, _ = _watch_with(lambda: next(fields, None))
    assert watcher.status()["counts"] == {"not_found": 1}


@pytest.mark.parametrize("reason", ["password", "terminal"])
def test_a_password_field_or_terminal_met_while_refetching_the_first_read_is_named(reason):
    calls = []

    def factory():
        calls.append(1)
        if len(calls) == 1:
            return DyingField([])
        raise FieldSkipped(reason)

    watcher, _ = _watch_with(factory)
    assert watcher.status()["counts"] == {reason: 1}


def test_the_refetch_stops_when_told_to():
    calls = []
    watcher = EditWatcher(lambda s: None, lambda: [], field_factory=lambda: calls.append(1))
    assert watcher._refetch(lambda f: "x", stop=lambda: True) is None
    assert calls == []


def test_the_refetch_checks_stop_between_tries():
    calls = []
    watcher = EditWatcher(lambda s: None, lambda: [], field_factory=lambda: calls.append(1) or FakeField(["x"]))
    states = iter([False, True])
    assert watcher._refetch(lambda f: None, stop=lambda: next(states)) is None
    assert len(calls) == 1


def test_watched_outcomes_are_derived_from_the_messages():
    skipped = {key for key, message in OUTCOMES.items() if message.startswith("skipped")}
    assert set(OUTCOMES) - skipped == edit_watch.WATCHED
    assert {"suggested", "unchanged", "lost_field"} <= edit_watch.WATCHED
    assert not {"terminal", "not_found", "unreadable"} & edit_watch.WATCHED
    assert EditWatcher(lambda s: None, lambda: []).status()["watched"] == list(edit_watch.WATCHED_KEYS)


def test_watcher_hands_every_outcome_to_the_store():
    stored = []
    watcher = EditWatcher(lambda s: None, lambda: [], field_factory=lambda: None, on_outcome=stored.append)
    watcher.skip("clipboard")
    watcher.watch("I use nexus fox", 0.05)
    deadline = time.monotonic() + 2
    while len(stored) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    watcher.stop()
    assert stored == ["clipboard", "no_field"]


def test_watcher_survives_a_failing_store():
    def broken(_outcome):
        raise RuntimeError("database is locked")

    watcher = EditWatcher(lambda s: None, lambda: [], on_outcome=broken)
    watcher.skip("terminal")
    assert watcher.status()["counts"] == {"terminal": 1}
