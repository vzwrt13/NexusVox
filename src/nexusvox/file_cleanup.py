"""Optional LLM cleanup for uploaded files: the transcript, tidied passage by passage.

Reuses the dictation cleanup (`cleanup.cleanup`, its prompts and its plausibility check) on pieces
of the transcript small enough for the model: one speaker block, or a run of sentences for a
transcript without speakers. The `[llm].timeout_s` applies to each piece, not the short dictation
timeout, and each request waits for live requests (dictation, polish) to finish first. A piece the
model fails on, answers implausibly or leaves alone keeps its own words, so the cleaned transcript
is always complete; the raw transcript is stored beside it, untouched. A transcript in which no
piece changed comes back as it was, character for character.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from . import llm as llm_module
from .cleanup import PROMPTS, cleanup
from .config import CleanupConfig, LlmConfig

logger = logging.getLogger(__name__)

# Words per model call. A long monologue is cut at sentence ends below this; a small model loses
# the thread (and the plausibility check its footing) on much more.
MAX_WORDS = 120
# A short speaker turn ("Um, yes.") still holds fillers; dictation cleanup skips fewer than 3 words.
MIN_PIECE_WORDS = 2
# Consecutive pieces the model failed on (not merely answered implausibly) before the rest is skipped:
# a server that is down would otherwise cost `[llm].timeout_s` for every remaining piece.
MAX_CONSECUTIVE_FAILURES = 3

_SPEAKER_RE = re.compile(r"(Speaker \d+: )(.*)", re.DOTALL)
_PARAGRAPH_GAP_RE = re.compile(r"(\n\s*\n)")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"\S+")
# Abbreviations (English and German) that end in a full stop in the middle of a sentence.
_ABBREVIATIONS = frozenset(
    {"dr.", "mr.", "mrs.", "ms.", "prof.", "nr.", "bzw.", "ca.", "vs.", "evtl.", "ggf.", "inkl."}
    | {"z.b.", "d.h.", "u.a.", "z.t.", "o.ä.", "i.d.r.", "i.e.", "e.g."}
    # The first half of a German abbreviation written with a space ("z. B."): rarely a sentence's last word.
    | {"z.", "d.", "u.", "o."}
)


@dataclass(frozen=True)
class Paragraph:
    """A block of the transcript: the "Speaker N: " label it starts with (or empty), its pieces, the
    whitespace between consecutive pieces and the whitespace that follows the block."""

    label: str
    pieces: list[str]
    seps: list[str]
    gap: str = ""


@dataclass(frozen=True)
class TranscriptCleanup:
    text: str  # the cleaned transcript; the original, unchanged, where nothing could be cleaned
    pieces: int  # how many pieces the transcript was cut into
    failed: int  # pieces the model failed on or answered implausibly, which keep their words
    skipped: int  # pieces never sent because the model kept failing

    @property
    def note(self) -> str:
        """What went wrong, for the job's notes; empty when every piece was cleaned."""
        if not (self.failed or self.skipped):
            return ""
        kept = self.failed + self.skipped
        note = f"Text cleanup could not clean {kept} of {self.pieces} passages; they keep their original wording."
        if self.skipped:
            note += " The language model kept failing, so the rest was not tried."
        return note


def _ends_in_abbreviation(sentence: str) -> bool:
    """Whether *sentence* ends in an abbreviation such as "Dr." or "z. B.", so a sentence goes on after it."""
    tokens = sentence.split()
    return any("".join(tokens[-n:]).lower() in _ABBREVIATIONS for n in (1, 2, 3) if len(tokens) >= n)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        if _ends_in_abbreviation(text[start : match.start()]):
            continue
        spans.append((start, match.start()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _cut_long_sentence(text: str, words: list[tuple[int, int]], max_words: int) -> list[tuple[int, int]]:
    """A sentence longer than *max_words* in spans of at most that many words, cut after a comma, semicolon
    or colon in the second half of a span where there is one (a cut mid-clause gets a false full stop)."""
    spans = []
    i = 0
    while len(words) - i > max_words:
        window = words[i : i + max_words]
        cut = max_words - 1
        for k in range(max_words - 1, max_words // 2 - 1, -1):
            if text[window[k][1] - 1] in ",;:":
                cut = k
                break
        spans.append((words[i][0], words[i + cut][1]))
        i += cut + 1
    spans.append((words[i][0], words[-1][1]))
    return spans


def _split(text: str, max_words: int) -> tuple[list[str], list[str]]:
    """*text* as pieces of its own characters, and the whitespace that was between consecutive pieces."""
    text = text.strip()
    spans: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    count = 0
    for start, end in _sentence_spans(text):
        words = [m.span() for m in _WORD_RE.finditer(text, start, end)]
        if not words:
            continue
        if current and count + len(words) > max_words:
            spans.append(current)
            current, count = None, 0
        if len(words) > max_words:
            spans.extend(_cut_long_sentence(text, words, max_words))
            continue
        current = (current[0] if current else words[0][0], words[-1][1])
        count += len(words)
    if current:
        spans.append(current)
    pieces = [text[a:b] for a, b in spans]
    seps = [text[spans[i][1] : spans[i + 1][0]] for i in range(len(spans) - 1)]
    return pieces, seps


def split_pieces(text: str, max_words: int = MAX_WORDS) -> list[str]:
    """*text* cut at sentence ends into pieces of at most *max_words* words (one word is never split).

    A sentence longer than that is cut after a comma where there is one, else by words.
    """
    return _split(text, max_words)[0]


def split_paragraphs(text: str, max_words: int = MAX_WORDS) -> list[Paragraph]:
    """The transcript's blank-line separated blocks; a "Speaker N: " label is split off and kept."""
    parts = _PARAGRAPH_GAP_RE.split(text.strip())
    paragraphs = []
    for i in range(0, len(parts), 2):
        block = parts[i]
        if not block.strip():
            continue
        label, body = "", block
        if match := _SPEAKER_RE.fullmatch(block):
            label, body = match.group(1), match.group(2)
        pieces, seps = _split(body, max_words)
        gap = parts[i + 1] if i + 1 < len(parts) else ""
        paragraphs.append(Paragraph(label, pieces, seps, gap))
    return paragraphs


def _join(paragraph: Paragraph, pieces: list[str]) -> str:
    text = paragraph.label + pieces[0] if pieces else paragraph.label
    for sep, piece in zip(paragraph.seps, pieces[1:], strict=True):
        text += sep + piece
    return text + paragraph.gap


def clean_transcript(
    text: str,
    level: str,
    llm: LlmConfig,
    on_progress: Callable[..., None] | None = None,
) -> TranscriptCleanup:
    """*text* cleaned up at *level*, piece by piece. Blocking HTTP; never raises.

    `on_progress("cleaning", current, total)` is called as piece `current` of `total` starts. A level
    without a prompt, or a transcript in which no piece changed, gives *text* back untouched.
    """
    paragraphs = split_paragraphs(text)
    total = sum(len(p.pieces) for p in paragraphs)
    if level not in PROMPTS or not total:
        return TranscriptCleanup(text, total, 0, 0)
    config = CleanupConfig(level=level, timeout_s=llm.timeout_s)
    done = failed = skipped = streak = 0
    changed = False
    cleaned_paragraphs: list[str] = []
    with llm_module.background():
        for paragraph in paragraphs:
            cleaned_pieces = []
            for piece in paragraph.pieces:
                done += 1
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    skipped += 1
                    cleaned_pieces.append(piece)
                    continue
                if on_progress:
                    on_progress("cleaning", done, total)
                result = cleanup(piece, config, llm, MIN_PIECE_WORDS)
                if result.error:
                    failed += 1
                if result.ms is not None:
                    streak = 0  # the model answered, sensibly or not
                elif result.error:
                    streak += 1  # the request failed; a piece too short to send changes nothing
                changed = changed or result.text != piece
                cleaned_pieces.append(result.text)
            cleaned_paragraphs.append(_join(paragraph, cleaned_pieces))
    logger.info("Upload cleanup (%s): %d pieces, %d kept as spoken, %d skipped", level, total, failed, skipped)
    if not changed:
        return TranscriptCleanup(text, total, failed, skipped)
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return TranscriptCleanup(lead + "".join(cleaned_paragraphs).rstrip() + trail, total, failed, skipped)
