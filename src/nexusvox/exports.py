"""Built-in export targets for stored file transcriptions: plain text, SRT and WebVTT subtitles.

They are registered through the same `ExportTarget` mechanism as an extension's (see `load_extensions`), so the
Upload tab shows them as download links next to every stored upload. The subtitle cues are built from the
per-word timing (`FileTranscript.words`); an upload without it cannot be exported as subtitles.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from .extensions import ExportTarget, FileTranscript
from .meeting_transcribe import SPEAKER_LABELS
from .transcriber import Word

# `(transcript, texts) -> texts` of the same length: `ExtensionRegistry.display_texts`.
TextTransform = Callable[[FileTranscript, list[str]], list[str]]

MAX_CUE_S = 7.0  # a cue stays on screen for at most this long
MAX_LINE_CHARS = 42  # the usual subtitle line width
MAX_LINES = 2
PAUSE_S = 1.0  # a silence this long between two words of one speaker ends the cue
SENTENCE_END = (".", "!", "?", "…")
SUBTITLE_TARGET_IDS = ("srt", "vtt")
NO_TIMING_HINT = "No word timestamps stored (Parakeet provides them)"


def _speaker_label(transcript: FileTranscript, speaker: int) -> str:
    if transcript.kind == "meeting" and speaker in SPEAKER_LABELS:
        return SPEAKER_LABELS[speaker]
    return f"Speaker {speaker}"


def _split_lines(text: str) -> list[str] | None:
    """*text* as one line, or two of about equal length, none over MAX_LINE_CHARS; None when that is impossible."""
    if len(text) <= MAX_LINE_CHARS:
        return [text]
    words = text.split()
    best: list[str] | None = None
    best_gap = len(text)
    for i in range(1, len(words)):
        first, second = " ".join(words[:i]), " ".join(words[i:])
        gap = abs(len(first) - len(second))
        if max(len(first), len(second)) <= MAX_LINE_CHARS and gap < best_gap:
            best, best_gap = [first, second], gap
    return best


def _wrap(text: str) -> str:
    """Break *text* into lines; a word longer than a line is cut so that no line exceeds MAX_LINE_CHARS."""
    lines = _split_lines(text)
    if lines is not None:
        return "\n".join(lines)
    pieces: list[str] = []
    for word in text.split():
        while len(word) > MAX_LINE_CHARS:
            pieces.append(word[:MAX_LINE_CHARS])
            word = word[MAX_LINE_CHARS:]
        pieces.append(word)
    return "\n".join(_split_lines(" ".join(pieces)) or pieces)


def _text(words: list[Word]) -> str:
    return " ".join(w.word.strip() for w in words)


def _cut(words: list[Word]) -> list[list[Word]]:
    """One speaker's words as cues: a cue ends at a pause of PAUSE_S, the end of a sentence once it is long enough,
    or when the next word would push it past MAX_CUE_S or out of MAX_LINES lines of MAX_LINE_CHARS."""
    cues: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current and (
            word.start - current[-1].end >= PAUSE_S
            or word.end - current[0].start > MAX_CUE_S
            or _split_lines(_text([*current, word])) is None
            or (current[-1].word.strip().endswith(SENTENCE_END) and len(_text(current)) > MAX_LINE_CHARS // 2)
        ):
            cues.append(current)
            current = []
        current.append(word)
    if current:
        cues.append(current)
    return cues


def build_cues(transcript: FileTranscript) -> list[tuple[float, float, str]]:
    """`(start, end, text)` per cue, in seconds, ordered by start.

    Each speaker's words are cut into cues on their own (see `_cut`), so two people talking over each other give
    two overlapping cue streams instead of a flurry of one-word cues. A word without a speaker (no diarization
    turn covered it) belongs to the speaker before it. A cue gets the speaker's label when the cue before it
    (in start order) is from somebody else.
    """
    words = [w for w in transcript.words if w.word.strip()]
    streams: dict[int | None, list[Word]] = {}
    last: int | None = None
    for word in words:
        speaker = word.speaker if word.speaker is not None else last
        last = speaker
        streams.setdefault(speaker, []).append(word)
    cues = sorted(
        ((speaker, cue) for speaker, stream in streams.items() for cue in _cut(stream)),
        key=lambda item: (item[1][0].start, item[1][0].end),
    )

    out: list[tuple[int | None, float, float, str]] = []
    previous: int | None = None
    queue = list(cues)
    while queue:
        speaker, cue = queue.pop(0)
        label = f"{_speaker_label(transcript, speaker)}: " if speaker is not None and speaker != previous else ""
        # The label takes room: words that no longer fit move to a cue of their own.
        keep = len(cue)
        while keep > 1 and _split_lines(label + _text(cue[:keep])) is None:
            keep -= 1
        if keep < len(cue):
            rest = cue[keep:]
            cue = cue[:keep]
            # The overflow joins the speaker's next cue (re-cut together), so no stub cue is left behind.
            following = next((i for i, (who, _) in enumerate(queue) if who == speaker), None)
            if following is not None:
                rest = rest + queue.pop(following)[1]
            queue = sorted(
                [*queue, *((speaker, part) for part in _cut(rest))], key=lambda item: (item[1][0].start, item[1][0].end)
            )
        previous = speaker
        out.append((speaker, cue[0].start, max(cue[-1].end, cue[0].start), _wrap(label + _text(cue))))

    # A cue never runs into the next one of the same speaker; different speakers may overlap on purpose.
    result = []
    for i, (speaker, start, end, text) in enumerate(out):
        for later_speaker, later_start, _, _ in out[i + 1 :]:
            if later_speaker == speaker:
                if start < later_start < end:
                    end = later_start
                break
        result.append((start, end, text))
    return result


def _timestamp(seconds: float, separator: str) -> str:
    total_ms = round(max(seconds, 0.0) * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{ms:03d}"


def _require_words(transcript: FileTranscript) -> None:
    if not any(w.word.strip() for w in transcript.words):
        raise ValueError(f"No readable word timestamps are stored for this {transcript.kind} (Parakeet provides them)")


def _displayed_cues(transcript: FileTranscript, transform: TextTransform | None) -> list[tuple[float, float, str]]:
    cues = build_cues(transcript)
    if transform is None:
        return cues
    texts = transform(transcript, [text for _, _, text in cues])
    if len(texts) != len(cues):  # a transform that loses cues is not trusted
        return cues
    return [(start, end, text) for (start, end, _), text in zip(cues, texts, strict=True)]


def to_txt(transcript: FileTranscript, transform: TextTransform | None = None) -> str:
    """The transcript as shown: cleaned when cleanup ran, with `Speaker N:` paragraphs when speakers were detected."""
    text = transcript.best_text
    if transform is not None:
        text = transform(transcript, [text])[0]
    return text.rstrip() + "\n"


def to_srt(transcript: FileTranscript, transform: TextTransform | None = None) -> str:
    _require_words(transcript)
    blocks = [
        f"{n}\n{_timestamp(start, ',')} --> {_timestamp(end, ',')}\n{text}"
        for n, (start, end, text) in enumerate(_displayed_cues(transcript, transform), 1)
    ]
    return "\n\n".join(blocks) + "\n"


def to_vtt(transcript: FileTranscript, transform: TextTransform | None = None) -> str:
    _require_words(transcript)
    blocks = [
        f"{_timestamp(start, '.')} --> {_timestamp(end, '.')}\n{text.replace('&', '&amp;').replace('<', '&lt;')}"
        for start, end, text in _displayed_cues(transcript, transform)
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def builtin_export_targets(transform: TextTransform | None = None) -> list[ExportTarget]:
    """TXT, SRT and VTT; *transform* (the registry's `display_texts`) changes the texts they write."""
    return [
        ExportTarget("txt", "TXT", ".txt", "text/plain; charset=utf-8", partial(to_txt, transform=transform)),
        ExportTarget("srt", "SRT", ".srt", "application/x-subrip; charset=utf-8", partial(to_srt, transform=transform)),
        ExportTarget("vtt", "VTT", ".vtt", "text/vtt; charset=utf-8", partial(to_vtt, transform=transform)),
    ]
