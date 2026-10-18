"""Learn dictionary entries from what the user changes after a paste.

After a dictation is pasted, `EditWatcher` keeps reading the focused text field
through UI Automation for `[learn_from_edits].window_s` seconds. When the window
ends (or the next dictation starts), the pasted span is located in the field's
text from right after the paste, mapped onto the field's latest text, and the
two versions are diffed word by word (`dictionary.suggest_entries`). Changed
words become *pending suggestions* in the database; the user accepts or
dismisses them in the dashboard, nothing is added to the dictionary on its own.

Password fields are never read. All UI Automation calls run on the watcher's own
thread, so no COM pointer crosses threads and the hotkey hook is never blocked.
`extract_edit` and `learn` are pure and tested without UI Automation.
"""

from __future__ import annotations

import difflib
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .dictionary import DictionaryEntry, suggest_entries

logger = logging.getLogger(__name__)

# A field longer than this is not watched (large documents, chat logs).
MAX_FIELD_CHARS = 20_000
# Below this similarity the user rewrote the text rather than fixing words in it.
MIN_SIMILARITY = 0.5
# Share of the dictated words that must survive the edit (a misheard name keeps the rest).
MIN_WORDS_KEPT = 0.5
# More changed places than this is rewording, not fixing misheard terms.
MAX_CHANGES = 2
# Longer replacements are rephrasing, not misheard terms.
MAX_SUGGESTION_WORDS = 4
# Settle time before the first read, so the paste has landed in the field.
_SETTLE_S = 0.4
_POLL_S = 1.0
# How long the pasted text may take to show up in the field.
_PASTE_WAIT_S = 2.0

_UIA_VALUE_PATTERN_ID = 10002
_UIA_TEXT_PATTERN_ID = 10014


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


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


def extract_edit(before: str, injected: str, after: str) -> str | None:
    """The user's version of `injected`, or None when it cannot be told apart.

    `before` is the field text right after the paste, `after` the latest one.
    Returns None when the pasted text is not in `before`, when it was deleted,
    or when it was rewritten beyond `MIN_SIMILARITY`.
    """
    before, injected, after = _normalize(before), _normalize(injected), _normalize(after)
    if not injected.strip():
        return None
    start = before.rfind(injected)
    if start < 0:
        return None
    end = start + len(injected)
    opcodes = _char_opcodes(before, after, start, end)
    edited = after[_map_position(start, opcodes, start=True) : _map_position(end, opcodes, start=False)]
    if not edited.strip():
        return None
    if difflib.SequenceMatcher(a=injected, b=edited, autojunk=False).ratio() < MIN_SIMILARITY:
        return None
    return edited


def learn(before: str, injected: str, after: str, existing: list[DictionaryEntry]) -> list[DictionaryEntry]:
    """Dictionary suggestions from one watched paste."""
    edited = extract_edit(before, injected, after)
    if edited is None or edited == _normalize(injected):
        return []
    # Fixing a misheard term keeps most words and touches one or two places; a rewrite does not.
    old_words = injected.split()
    words = difflib.SequenceMatcher(a=old_words, b=edited.split(), autojunk=False)
    kept = sum(size for _, _, size in words.get_matching_blocks())
    changes = sum(1 for tag, *_ in words.get_opcodes() if tag != "equal")
    if kept / len(old_words) < MIN_WORDS_KEPT or changes > MAX_CHANGES:
        return []
    return [
        e
        for e in suggest_entries(_normalize(injected), edited, existing)
        if len(e.wrong.split()) <= MAX_SUGGESTION_WORDS and len(e.right.split()) <= MAX_SUGGESTION_WORDS
    ]


class TextField(Protocol):
    def read(self) -> str | None:
        """Current text of the field, or None when it can no longer be read."""


class UiaTextField:
    """The element that had keyboard focus when it was created, read through UI Automation."""

    def __init__(self, uia, automation, element) -> None:
        self._uia = uia
        self._automation = automation
        self._element = element

    @classmethod
    def focused(cls) -> UiaTextField | None:
        """The focused element, or None when there is none or it is a password field."""
        import comtypes.client

        uia = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        element = automation.GetFocusedElement()
        if element is None or element.CurrentIsPassword:
            return None
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
        except Exception:
            logger.debug("Learn from edits: field no longer readable", exc_info=True)
        return None


@dataclass
class _Watch:
    injected: str
    window_s: float


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
    ) -> None:
        self._on_suggestions = on_suggestions
        self._existing = existing
        self._field_factory = field_factory
        self._queue: queue.Queue[_Watch | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def watch(self, injected: str, window_s: float) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="edit-watch", daemon=True)
            self._thread.start()
        self._queue.put(_Watch(injected, window_s))

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
        try:
            field = self._field_factory()
            before = self._read_pasted(field, item.injected) if field else None
        except Exception:
            logger.exception("Learn from edits: reading the focused field failed")
            field, before = None, None
        if field is None or before is None:
            logger.debug("Learn from edits: pasted text not found in the focused field")
            return self._queue.get()
        after = before
        deadline = time.monotonic() + item.window_s
        nxt: _Watch | None | bool = False
        while time.monotonic() < deadline:
            try:
                nxt = self._queue.get(timeout=_POLL_S)
                break
            except queue.Empty:
                pass
            current = field.read()
            if current is None:
                break
            if len(current) <= MAX_FIELD_CHARS:
                after = current
        self._report(before, item.injected, after)
        return self._queue.get() if nxt is False else nxt

    @staticmethod
    def _read_pasted(field: TextField, injected: str) -> str | None:
        """The field text once the paste shows up in it (apps paste asynchronously), or None."""
        wanted = _normalize(injected)
        deadline = time.monotonic() + _PASTE_WAIT_S
        while True:
            text = field.read()
            if text is not None and len(text) <= MAX_FIELD_CHARS and wanted in _normalize(text):
                return text
            if text is None or time.monotonic() >= deadline:
                return None
            time.sleep(_POLL_S / 5)

    def _report(self, before: str, injected: str, after: str) -> None:
        try:
            suggestions = learn(before, injected, after, self._existing())
        except Exception:
            logger.exception("Learn from edits: diff failed")
            return
        if suggestions:
            logger.info("Learn from edits: %d suggestion(s)", len(suggestions))
            self._on_suggestions(suggestions)
