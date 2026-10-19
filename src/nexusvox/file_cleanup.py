"""Optional LLM cleanup for uploaded files: the transcript, tidied passage by passage.

Reuses the dictation cleanup (`cleanup.cleanup`, its prompts and its plausibility check) on pieces
of the transcript small enough for the model: one speaker block, or a run of sentences for a
transcript without speakers. The `[llm].timeout_s` applies to each piece, not the short dictation
timeout. A piece the model fails on, answers implausibly or leaves alone keeps its own words, so
the cleaned transcript is always complete; the raw transcript is stored beside it, untouched.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from .cleanup import PROMPTS, cleanup
from .config import CleanupConfig, LlmConfig

logger = logging.getLogger(__name__)

# Words per model call. A long monologue is cut at sentence ends below this; a small model loses
# the thread (and the plausibility check its footing) on much more.
MAX_WORDS = 120
# Consecutive pieces the model failed on (not merely answered implausibly) before the rest is skipped:
# a server that is down would otherwise cost `[llm].timeout_s` for every remaining piece.
MAX_CONSECUTIVE_FAILURES = 3

_SPEAKER_RE = re.compile(r"(Speaker \d+: )(.*)", re.DOTALL)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_IMPLAUSIBLE = "implausible reply"


@dataclass(frozen=True)
class Paragraph:
    """A block of the transcript: the "Speaker N: " label it starts with (or empty) and its pieces."""

    label: str
    pieces: list[str]


@dataclass(frozen=True)
class TranscriptCleanup:
    text: str  # the cleaned transcript; the original where nothing could be cleaned
    pieces: int  # how many model calls the transcript needed
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


def split_pieces(text: str, max_words: int = MAX_WORDS) -> list[str]:
    """*text* cut at sentence ends into pieces of at most *max_words* words (one word is never split)."""
    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in _SENTENCE_END_RE.split(text.strip()):
        words = sentence.split()
        if not words:
            continue
        if current and count + len(words) > max_words:
            pieces.append(" ".join(current))
            current, count = [], 0
        while len(words) > max_words:  # a sentence longer than a piece: cut it at the limit
            pieces.append(" ".join(words[:max_words]))
            words = words[max_words:]
        current.extend(words)
        count += len(words)
    if current:
        pieces.append(" ".join(current))
    return pieces


def split_paragraphs(text: str, max_words: int = MAX_WORDS) -> list[Paragraph]:
    """The transcript's blank-line separated blocks; a "Speaker N: " label is split off and kept."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        if not block.strip():
            continue
        label, body = "", block
        if match := _SPEAKER_RE.fullmatch(block.strip()):
            label, body = match.group(1), match.group(2)
        paragraphs.append(Paragraph(label, split_pieces(body, max_words)))
    return paragraphs


def clean_transcript(
    text: str,
    level: str,
    llm: LlmConfig,
    on_progress: Callable[..., None] | None = None,
) -> TranscriptCleanup:
    """*text* cleaned up at *level*, piece by piece. Blocking HTTP; never raises.

    `on_progress("cleaning", current, total)` is called as piece `current` of `total` starts.
    """
    paragraphs = split_paragraphs(text)
    total = sum(len(p.pieces) for p in paragraphs)
    if level not in PROMPTS or not total:
        return TranscriptCleanup(text, total, 0, 0)
    config = CleanupConfig(level=level, timeout_s=llm.timeout_s)
    done = failed = skipped = streak = 0
    cleaned_paragraphs: list[str] = []
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
            result = cleanup(piece, config, llm)
            if result.error:
                failed += 1
                streak = streak + 1 if result.error != _IMPLAUSIBLE else 0
            else:
                streak = 0
            cleaned_pieces.append(result.text)
        cleaned_paragraphs.append(paragraph.label + " ".join(cleaned_pieces))
    logger.info("Upload cleanup (%s): %d pieces, %d kept as spoken, %d skipped", level, total, failed, skipped)
    return TranscriptCleanup("\n\n".join(cleaned_paragraphs), total, failed, skipped)
