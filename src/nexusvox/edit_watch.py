"""Learn dictionary entries from what the user changes after a paste.

After a dictation is pasted, `EditWatcher` keeps reading the focused text field
through UI Automation for `[learn_from_edits].window_s` seconds. When the window
ends (or the next dictation starts), the pasted span is located in the field's
text from right after the paste, mapped onto the field's latest text, and the
two versions are diffed word by word (`dictionary.suggest_entries`). Changed
words become *pending suggestions* in the database; the user accepts or
dismisses them in the dashboard, nothing is added to the dictionary on its own.

Password fields and terminals are never read: a terminal's input is an empty
helper element (xterm.js) or a hard-wrapped screen buffer, so the paste cannot be
found there. Every paste ends in one outcome (`OUTCOMES`), logged at INFO with the
numbers behind it and how the watch ended, and counted for the dashboard, so
"learned nothing" always has a stated reason. When a read fails, the focused element
is fetched again before the watch gives up; the last successful read is still judged.
A field cleared on send is judged by the last text that still held the dictation.

All UI Automation calls run on the watcher's own thread, so no COM pointer
crosses threads and the hotkey hook is never blocked. `extract_edit` and `learn`
are pure and tested without UI Automation.
"""

from __future__ import annotations

import difflib
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from .dictionary import DictionaryEntry, apply_dictionary, suggest_entries
from .lang_detect import language_guess
from .uia import automation as uia_automation
from .uia import foreground_is_terminal, is_terminal_element

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# A field longer than this is not watched (large documents, chat logs).
MAX_FIELD_CHARS = 20_000
# Below this similarity the user rewrote the text rather than fixing words in it.
MIN_SIMILARITY = 0.5
# Share of the dictated words that must survive the edit (a misheard name keeps the rest).
MIN_WORDS_KEPT = 0.5
# More changed places than this is rewording, not fixing misheard terms.
MAX_CHANGES = 2
# lingua confidence above which one side of a correction counts as clearly in its language.
_SURE_LANGUAGE = 0.9
# Longer replacements are rephrasing, not misheard terms.
MAX_SUGGESTION_WORDS = 4
# Settle time before the first read, so the paste has landed in the field.
_SETTLE_S = 0.4
_POLL_S = 1.0
# How long the pasted text may take to show up in the field.
_PASTE_WAIT_S = 2.0
# How often the focused element is fetched again after a failed read (a browser rebuilds its address bar).
_REFETCH_TRIES = 3
# A re-fetched element much longer than the field it replaces is a page, not that field:
# it may hold at most twice the text of the first read plus this many characters.
_REFETCH_GROWTH_CHARS = 500
# Share of the first read a diffed re-fetched element must still match to count as the same field.
_REFETCH_MIN_SIMILARITY = 0.6
# Fields up to this size may be diffed to decide whether a re-fetched element is ours (bigger ones are not).
_REFETCH_DIFF_MAX_CHARS = 2_000

_UIA_VALUE_PATTERN_ID = 10002
_UIA_TEXT_PATTERN_ID = 10014

# How a paste ended for learning, and how the log and the dashboard say it.
OUTCOMES = {
    "suggested": "suggestions found",
    "unchanged": "watched, the dictation was not changed",
    "too_small": "watched, nothing learned: the edit was too small (only line breaks or spacing changed)",
    "rewritten": "watched, nothing learned: the dictation was deleted or rewritten",
    "too_many_changes": "watched, nothing learned: too many changes, this looks like rewording",
    "no_replacement": "watched, nothing learned: no word was replaced by another",
    "lost_field": "watched, nothing learned: the field became unreadable before an edit was seen",
    "processed": "skipped: auto cleanup or translation changed the text",
    "clipboard": "skipped: the paste failed",
    "terminal": "skipped: terminals are not watched",
    "password": "skipped: password field",
    "no_field": "skipped: no focused element",
    "unreadable": "skipped: the focused element does not expose its text",
    "too_long": f"skipped: the field holds more than {MAX_FIELD_CHARS:,} characters",
    "not_found": "skipped: the pasted text did not show up in the field",
}
# Outcomes of a watch that ran to its end: every one whose message does not say "skipped".
WATCHED = frozenset(key for key, message in OUTCOMES.items() if not message.startswith("skipped"))
# The same, in the order and shape `EditWatcher.status()` hands to the dashboard.
WATCHED_KEYS = tuple(sorted(WATCHED))

# How a watch ended, as the log words it.
_ENDED = {
    "window": "time window over",
    "next_dictation": "the next dictation arrived",
    "unreadable": "the field could not be read any more",
}


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _line_squash(text: str) -> str:
    """`text` with every run of whitespace inside a line squashed, the line breaks kept."""
    return "\n".join(_squash(line) for line in text.split("\n"))


def _find_last(text: str, injected: str) -> tuple[int, int] | None:
    """Span of the last occurrence of `injected` in `text`, any run of whitespace matching any other.

    Fields re-wrap or re-indent pasted text (soft line breaks, a non-breaking space
    for a space), so an exact search would miss a paste that is plainly there.
    """
    words = injected.split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(map(re.escape, words)))
    # Step one character past each hit, not past its end: finditer skips overlapping
    # occurrences, so "ha ha" in "ha ha ha" would end at the first one, unlike rfind.
    last, match = None, pattern.search(text)
    while match:
        last = match.span()
        match = pattern.search(text, match.start() + 1)
    return last


def _map_position(pos: int, opcodes: list[tuple[str, int, int, int, int]], *, start: bool) -> int:
    """Map a position in the old text onto the new text.

    Text inserted exactly at the boundary of the span stays outside it, so typing
    on after the dictation does not count as an edit of the dictation.
    """
    for tag, i1, i2, j1, j2 in opcodes:
        if i1 == i2:
            continue
        inside = i1 <= pos < i2 if start else i1 < pos <= i2
        if not inside:
            continue
        if tag == "equal":
            return j1 + (pos - i1)
        return j1 if start else j2
    return opcodes[-1][4] if opcodes else 0


def _char_opcodes(a: str, b: str, span_start: int, span_end: int) -> list[tuple[str, int, int, int, int]]:
    """Character diff opcodes of `a` -> `b`, diffing only the part between the common prefix and suffix.

    A field of up to `MAX_FIELD_CHARS` usually changes in one small place; a full
    `SequenceMatcher` over both texts would spend seconds on the unchanged rest.
    The trim stops at `a[span_start:span_end]`, so the span is always diffed as a
    whole and keeps the alignment a full diff would give it.
    """
    limit = min(span_start, len(b))
    prefix = 0
    while prefix < limit and a[prefix] == b[prefix]:
        prefix += 1
    limit = min(len(a) - span_end, len(b) - prefix)
    suffix = 0
    while suffix < limit and a[len(a) - 1 - suffix] == b[len(b) - 1 - suffix]:
        suffix += 1
    a_end, b_end = len(a) - suffix, len(b) - suffix
    opcodes: list[tuple[str, int, int, int, int]] = []
    if prefix:
        opcodes.append(("equal", 0, prefix, 0, prefix))
    middle = difflib.SequenceMatcher(a=a[prefix:a_end], b=b[prefix:b_end], autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in middle:
        opcodes.append((tag, i1 + prefix, i2 + prefix, j1 + prefix, j2 + prefix))
    if suffix:
        opcodes.append(("equal", a_end, len(a), b_end, len(b)))
    return opcodes


@dataclass(frozen=True)
class _Edit:
    """The user's version of the pasted span; `original` is the span as `before` holds it."""

    edited: str | None
    original: str = ""
    ratio: float | None = None  # similarity of dictation and edit, None when the span was deleted
    found: bool = True  # False when the dictation is not in `before` at all


def _edit_of(before: str, injected: str, after: str) -> _Edit:
    before, after = _normalize(before), _normalize(after)
    span = _find_last(before, injected)
    if span is None:
        return _Edit(None, found=False)
    start, end = span
    opcodes = _char_opcodes(before, after, start, end)
    edited = after[_map_position(start, opcodes, start=True) : _map_position(end, opcodes, start=False)]
    if not edited.strip():
        return _Edit(None, before[start:end])
    ratio = difflib.SequenceMatcher(a=_squash(injected), b=_squash(edited), autojunk=False).ratio()
    return _Edit(edited if ratio >= MIN_SIMILARITY else None, before[start:end], ratio)


def extract_edit(before: str, injected: str, after: str) -> str | None:
    """The user's version of `injected`, or None when it cannot be told apart.

    `before` is the field text right after the paste, `after` the latest one.
    Returns None when the pasted text is not in `before`, when it was deleted,
    or when it was rewritten beyond `MIN_SIMILARITY`.
    """
    return _edit_of(before, injected, after).edited


@dataclass(frozen=True)
class Verdict:
    """What one watched paste taught: `outcome` is a key of `OUTCOMES`, `detail` the numbers behind it."""

    outcome: str
    suggestions: list[DictionaryEntry]
    detail: str = ""


def judge(
    before: str, injected: str, after: str, existing: list[DictionaryEntry], spoken: str | None = None
) -> Verdict:
    """Dictionary suggestions from one watched paste, or the reason there are none.

    `spoken` is the text the dictionary will see again (the transcript after the
    dictionary, snippets as placeholders). Voice commands and snippets make the paste
    differ from it, so only an entry that matches `spoken` is kept: one for "25" or
    for a snippet's stored text would never match a transcript.
    """
    edit = _edit_of(before, injected, after)
    if not edit.found:
        return Verdict("not_found", [], "the dictation is not in the text the watch started from")
    if edit.edited is None:
        if edit.ratio is None:
            return Verdict("rewritten", [], "the dictation is gone from the field")
        return Verdict("rewritten", [], f"{edit.ratio:.0%} of it is left, {MIN_SIMILARITY:.0%} needed")
    edited = edit.edited
    if _squash(edited) == _squash(injected):
        # Only whitespace differs. A different run of spaces (a non-breaking space, a re-built field) is
        # no edit; moved line breaks are, against the span as the field held it right after the paste.
        same_lines = _line_squash(edited) == _line_squash(edit.original)
        return Verdict("unchanged" if same_lines else "too_small", [])
    # Fixing a misheard term keeps most words and touches one or two places; a rewrite does not.
    old_words = injected.split()
    words = difflib.SequenceMatcher(a=old_words, b=edited.split(), autojunk=False)
    kept = sum(size for _, _, size in words.get_matching_blocks())
    changes = sum(1 for tag, *_ in words.get_opcodes() if tag != "equal")
    if kept / len(old_words) < MIN_WORDS_KEPT or changes > MAX_CHANGES:
        detail = f"{changes} changed places (at most {MAX_CHANGES}), {kept} of {len(old_words)} words kept"
        return Verdict("too_many_changes", [], detail)
    candidates = suggest_entries(_normalize(injected), edited, existing)
    suggestions = [
        e
        for e in candidates
        if len(e.wrong.split()) <= MAX_SUGGESTION_WORDS
        and len(e.right.split()) <= MAX_SUGGESTION_WORDS
        and not _language_switch(e)
        and (spoken is None or apply_dictionary(spoken, [e]) != spoken)
    ]
    if suggestions:
        return Verdict("suggested", suggestions)
    if candidates:
        detail = "the replacement is too long, mixes languages or does not match the words that were said"
    else:
        detail = "words were only added or removed, or the replacement is already in the dictionary"
    return Verdict("no_replacement", [], detail)


def learn(
    before: str, injected: str, after: str, existing: list[DictionaryEntry], spoken: str | None = None
) -> list[DictionaryEntry]:
    """Dictionary suggestions from one watched paste (see `judge`)."""
    return judge(before, injected, after, existing, spoken).suggestions


def _language_switch(entry: DictionaryEntry) -> bool:
    """True when the model heard the wrong language ("jetzt aber" -> "it's our"), not a wrong word.

    Such an entry would rewrite every genuine English "it's our" into German. Only
    phrases of two words or more are judged; single words and names are too short
    for a language guess. Short phrases rarely get a confident guess on both sides
    ("it's our" is only 0.62 English), so the two sides leaning to different
    languages with one of them sure is enough.
    """
    if len(entry.wrong.split()) < 2 or len(entry.right.split()) < 2:
        return False
    (wrong, wrong_conf), (right, right_conf) = language_guess(entry.wrong), language_guess(entry.right)
    return "unknown" not in (wrong, right) and wrong != right and max(wrong_conf, right_conf) >= _SURE_LANGUAGE


class FieldSkipped(Exception):
    """The focused element is deliberately not watched; `reason` is a key of `OUTCOMES`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TextField(Protocol):
    def read(self) -> str | None:
        """Current text of the field, or None when it can no longer be read."""


class UiaTextField:
    """The element that had keyboard focus when it was created, read through UI Automation."""

    def __init__(self, uia, automation, element) -> None:
        self._uia = uia
        self._automation = automation
        self._element = element
        self.last_error: Exception | None = None  # why the last read failed, for the watcher's log

    @classmethod
    def focused(cls) -> UiaTextField | None:
        """The focused element, or None when there is none.

        Raises `FieldSkipped` for a password field or a terminal.
        """
        if foreground_is_terminal():
            raise FieldSkipped("terminal")
        uia, automation = uia_automation()
        element = automation.GetFocusedElement()
        if element is None:
            return None
        if element.CurrentIsPassword:
            raise FieldSkipped("password")
        if is_terminal_element(element):
            raise FieldSkipped("terminal")
        return cls(uia, automation, element)

    def read(self) -> str | None:
        uia = self._uia
        try:
            pattern = self._element.GetCurrentPattern(_UIA_VALUE_PATTERN_ID)
            if pattern:
                return pattern.QueryInterface(uia.IUIAutomationValuePattern).CurrentValue
            pattern = self._element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID)
            if pattern:
                text_pattern = pattern.QueryInterface(uia.IUIAutomationTextPattern)
                return text_pattern.DocumentRange.GetText(MAX_FIELD_CHARS + 1)
        except Exception as exc:
            # The watcher announces a dead field once per watch; here every failed poll is only DEBUG.
            self.last_error = exc
            logger.debug("Learn from edits: reading the field failed: %s", exc)
        return None


@dataclass
class _Watch:
    injected: str
    window_s: float
    spoken: str | None


class EditWatcher:
    """Watches the field of the last paste on a worker thread and reports suggestions.

    `watch()` returns at once. A new `watch()` ends the running one early (its
    result still counts), because the next dictation usually means the user is
    done fixing the previous one.
    """

    def __init__(
        self,
        on_suggestions: Callable[[list[DictionaryEntry]], None],
        existing: Callable[[], list[DictionaryEntry]],
        field_factory: Callable[[], TextField | None] = UiaTextField.focused,
        on_outcome: Callable[[str], None] | None = None,
    ) -> None:
        self._on_suggestions = on_suggestions
        self._on_outcome = on_outcome
        self._existing = existing
        self._field_factory = field_factory
        self._queue: queue.Queue[_Watch | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._last: tuple[str, float] | None = None  # outcome, time.time()

    def watch(self, injected: str, window_s: float, spoken: str | None = None) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="edit-watch", daemon=True)
            self._thread.start()
        self._queue.put(_Watch(injected, window_s, spoken))

    def skip(self, outcome: str) -> None:
        """Record a paste the app does not hand to the watcher (a key of `OUTCOMES`)."""
        self._record(outcome)

    def status(self) -> dict:
        """Outcome counts since start and the last outcome, for the dashboard."""
        with self._lock:
            last = self._last
            return {
                "counts": dict(self._counts),
                "watched": list(WATCHED_KEYS),
                "last": {"outcome": last[0], "message": OUTCOMES[last[0]], "at": last[1]} if last else None,
            }

    def _record(self, outcome: str, detail: str = "") -> None:
        logger.info("Learn from edits: %s%s", OUTCOMES[outcome], f" ({detail})" if detail else "")
        with self._lock:
            self._counts[outcome] = self._counts.get(outcome, 0) + 1
            self._last = (outcome, time.time())
        if self._on_outcome is not None:
            try:
                self._on_outcome(outcome)
            except Exception:
                logger.exception("Could not store the learn-from-edits outcome")

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)

    def _run(self) -> None:
        try:
            from .mic_guard import _ensure_com

            _ensure_com()
        except Exception:
            logger.debug("Learn from edits: COM init skipped", exc_info=True)
        item = self._queue.get()
        while item is not None:
            try:
                item = self._watch_one(item)
            except Exception:
                # A dead worker would leave every later watch() queued forever.
                logger.exception("Learn from edits: watch failed")
                item = self._queue.get()

    def _watch_one(self, item: _Watch) -> _Watch | None:
        """Watch one paste; returns the next queued item (or None to stop)."""
        time.sleep(_SETTLE_S)
        announced = False

        def announce_dead(dead: TextField) -> None:
            """Say once per watch (INFO) that a field died; a field rebuilt on every poll must not flood the log."""
            nonlocal announced
            error = getattr(dead, "last_error", None)
            log = logger.debug if announced else logger.info
            log(
                "Learn from edits: reading the field failed%s, fetching the focused element again",
                f" ({error})" if error else "",
            )
            announced = True

        try:
            field = self._field_factory()
            before, skip = None, "no_field"
            if field:
                before, skip, died = self._read_pasted(field, item.injected)
                if died:
                    # The element may be dead already (a rebuilt address bar); the focused one may not be.
                    # `skip` keeps what the dead one showed (too long, no paste yet) if there is no other.
                    announce_dead(field)
                    refreshed = self._refetch(lambda f: self._read_pasted(f, item.injected, wait_s=0)[0])
                    if refreshed is not None:
                        field, before = refreshed
        except FieldSkipped as skipped:
            field, before, skip = None, None, skipped.reason
        except Exception:
            logger.exception("Learn from edits: reading the focused field failed")
            field, before, skip = None, None, "unreadable"
        if field is None or before is None:
            self._record(skip)
            return self._queue.get()
        after = before
        deadline = time.monotonic() + item.window_s
        nxt: _Watch | None | bool = False
        ended = "window"

        def must_stop() -> bool:
            return time.monotonic() >= deadline or not self._queue.empty()

        while time.monotonic() < deadline:
            try:
                nxt = self._queue.get(timeout=_POLL_S)
                ended = "next_dictation"
                break
            except queue.Empty:
                pass
            current = field.read()
            if current is None:
                # A browser rebuilds its address bar or page: the element we hold is dead, a new one may not be.
                announce_dead(field)
                try:
                    refreshed = self._refetch(lambda f: self._still_ours(f, item.injected, before), must_stop)
                except FieldSkipped:  # focus is in a password field or a terminal now
                    refreshed = None
                if refreshed is None:
                    if must_stop():
                        continue  # the window is over or the next dictation waits: the loop says which
                    ended = "unreadable"
                    break
                field, current = refreshed
            # A field cleared on send (or the dictation deleted) keeps the last text that still held it:
            # that is where the user's fixes are. Without an edit it equals `before`, so nothing is learned.
            if len(current) <= MAX_FIELD_CHARS and extract_edit(before, item.injected, current) is not None:
                after = current
        self._report(before, item, after, ended)
        return self._queue.get() if nxt is False else nxt

    def _refetch(
        self, accept: Callable[[TextField], _T | None], stop: Callable[[], bool] = lambda: False
    ) -> tuple[TextField, _T] | None:
        """The focused element again after a read failed, with what `accept` made of it.

        `accept` reads the element and returns None when it is not our field: focus that moved on to
        another field or page would otherwise be diffed as if the user had rewritten the paste.
        Gives up after `_REFETCH_TRIES`, or as soon as `stop()` says the watch must go on or end. A password
        field or a terminal (`FieldSkipped`) is passed on to the caller.
        """
        for _ in range(_REFETCH_TRIES):
            if stop():
                return None
            time.sleep(_POLL_S / 5)
            try:
                field = self._field_factory()
                accepted = accept(field) if field else None
            except FieldSkipped:
                raise
            except Exception:
                logger.debug("Learn from edits: fetching the focused element again failed", exc_info=True)
                continue
            if accepted is not None:
                logger.debug("Learn from edits: fetched the focused element again")
                return field, accepted
        return None

    @staticmethod
    def _still_ours(field: TextField, injected: str, before: str) -> str | None:
        """The field's text when it is still the field of the paste, as pasted or with the user's fixes, else None.

        A field that keeps being rebuilt is checked on every poll, so this stays cheap and strict: a
        much longer text is a page, not our field; the paste itself decides the rest, and only a small
        field is diffed against `before` (a short dictation of which two words were fixed). The words
        alone never decide: a results page holds the dictated words too.
        """
        text = field.read()
        if text is None or len(text) > MAX_FIELD_CHARS:
            return None
        if len(text) > 2 * len(before) + _REFETCH_GROWTH_CHARS:
            return None
        if _find_last(_normalize(text), injected) is not None:
            return text
        if len(text) > _REFETCH_DIFF_MAX_CHARS or len(before) > _REFETCH_DIFF_MAX_CHARS:
            return None
        # A fix of the dictation leaves most of the field as it was; another small text does not.
        same = difflib.SequenceMatcher(a=_squash(before), b=_squash(text), autojunk=False)
        if same.quick_ratio() < _REFETCH_MIN_SIMILARITY or same.ratio() < _REFETCH_MIN_SIMILARITY:
            return None
        return text if extract_edit(before, injected, text) is not None else None

    @staticmethod
    def _read_pasted(field: TextField, injected: str, wait_s: float | None = None) -> tuple[str | None, str, bool]:
        """The field text once the paste shows up in it (apps paste asynchronously).

        Waits up to `wait_s` for it (default `_PASTE_WAIT_S`, read at call time so tests can shorten it).

        Returns (text, "", False) or, when it does not show up, (None, the reason as a key of `OUTCOMES`,
        died). `died` says a read failed: the element is gone, and the reason is the last one seen before
        ("unreadable" when it never answered).
        """
        deadline = time.monotonic() + (_PASTE_WAIT_S if wait_s is None else wait_s)
        reason = "unreadable"
        while True:
            text = field.read()
            if text is None:
                return None, reason, True
            if len(text) > MAX_FIELD_CHARS:
                reason = "too_long"
            elif _find_last(_normalize(text), injected) is not None:
                return text, "", False
            else:
                reason = "not_found"
            if time.monotonic() >= deadline:
                return None, reason, False
            time.sleep(_POLL_S / 5)

    def _report(self, before: str, item: _Watch, after: str, ended: str = "window") -> None:
        try:
            verdict = judge(before, item.injected, after, self._existing(), item.spoken)
        except Exception:
            logger.exception("Learn from edits: diff failed")
            return
        outcome, detail = verdict.outcome, verdict.detail
        how = _ENDED[ended] if ended != "window" else f"{_ENDED[ended]}, {item.window_s:g} s"
        if ended == "unreadable" and outcome in ("unchanged", "too_small"):
            # No edit was ever seen, but the field died: "not changed" would claim more than we know.
            outcome = "lost_field"
        if outcome != "suggested":
            detail = f"{detail}; {how}" if detail else how
        else:
            detail = ""
        self._record(outcome, detail)
        if verdict.suggestions:
            logger.info("Learn from edits: %d suggestion(s)", len(verdict.suggestions))
            self._on_suggestions(verdict.suggestions)
