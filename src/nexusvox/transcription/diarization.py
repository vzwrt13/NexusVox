"""Speaker diarization for file uploads: merge speaker turns with word timestamps.

The diarization server (`docker/parakeet_server.py`, NVIDIA Streaming Sortformer) returns speaker
turns for the whole file; the transcriber returns per-word timing. Each word gets the speaker
whose turn holds its midpoint, and consecutive words of one speaker form a paragraph.
"""

from __future__ import annotations

from ..config import MODEL_REGISTRY, numbered_speaker_label
from .transcriber import SpeakerTurn, Word

# Diarization runs once over the whole file: a base for loading (or first downloading) the model,
# plus a share of the audio length. Generous, since Sortformer itself runs far faster than real time.
TIMEOUT_BASE_S = 300.0
TIMEOUT_PER_AUDIO_S = 0.5


def diarization_timeout(duration_s: float) -> float:
    """Timeout for one diarization request over a file of `duration_s` seconds."""
    return TIMEOUT_BASE_S + max(duration_s, 0.0) * TIMEOUT_PER_AUDIO_S


def speaker_detection_models() -> list[str]:
    """Display names of the models that can detect speakers (registry flag `diarization`)."""
    return [str(info["display_name"]) for info in MODEL_REGISTRY.values() if info.get("diarization")]


def speaker_at(word: Word, turns: list[SpeakerTurn]) -> int | None:
    """The model's speaker index for `word`: the turn that holds its midpoint.

    Overlapping turns (two people at once) go to the one that covers more of the word. A word in
    no turn (a pause the model heard as silence) goes to the nearest turn. None without turns.
    """
    if not turns:
        return None
    mid = (word.start + word.end) / 2
    holding = [t for t in turns if t.start <= mid <= t.end]
    if holding:
        return max(holding, key=lambda t: min(t.end, word.end) - max(t.start, word.start)).speaker
    return min(turns, key=lambda t: t.start - mid if mid < t.start else mid - t.end).speaker


def label_words(words: list[Word], turns: list[SpeakerTurn]) -> list[Word]:
    """`words` with `speaker` set to a display number (1, 2, ... in order of first appearance)."""
    numbers: dict[int, int] = {}
    labelled: list[Word] = []
    for word in words:
        index = speaker_at(word, turns)
        if index is None:
            labelled.append(word)
            continue
        number = numbers.setdefault(index, len(numbers) + 1)
        labelled.append(Word(word.word, word.start, word.end, speaker=number))
    return labelled


def speaker_runs(words: list[Word]) -> list[list[Word]]:
    """The runs of consecutive words of one speaker, in order; words without a speaker are left out.

    The one grouping behind `speaker_paragraphs` and `speaker_turns`: the dashboard pairs the turns with the
    paragraphs of the stored text by position, so the two must never split differently.
    """
    runs: list[list[Word]] = []
    for word in words:
        if word.speaker is None:
            continue
        if runs and runs[-1][0].speaker == word.speaker:
            runs[-1].append(word)
        else:
            runs.append([word])
    return runs


def speaker_paragraphs(words: list[Word]) -> list[tuple[int, str]]:
    """(speaker number, text) for each run of consecutive words of one speaker."""
    return [(run[0].speaker, " ".join(w.word for w in run)) for run in speaker_runs(words)]


def speaker_turns(words: list[Word]) -> list[tuple[int, float, float]]:
    """(speaker number, start, end) in seconds for each paragraph `speaker_paragraphs` makes, in the same order."""
    return [(run[0].speaker, run[0].start, run[-1].end) for run in speaker_runs(words)]


def format_transcript(words: list[Word], names: dict[int, str] | None = None) -> str:
    """The labelled transcript: "Speaker 1: ..." paragraphs separated by a blank line.

    `names` maps a speaker number to its label (a meeting names its first speaker after the own-voice setting).
    """
    names = names or {}
    return "\n\n".join(
        f"{names.get(speaker, numbered_speaker_label(speaker))}: {text}" for speaker, text in speaker_paragraphs(words)
    )
