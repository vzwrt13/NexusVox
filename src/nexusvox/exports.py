"""Built-in export targets for stored file transcriptions: plain text, SRT and WebVTT subtitles.

They are registered through the same `ExportTarget` mechanism as an extension's (see `load_extensions`), so the
Upload tab shows them as download links next to every stored upload. The subtitle cues are built from the
per-word timing (`FileTranscript.words`); an upload without it cannot be exported as subtitles.
"""

from __future__ import annotations

from .extensions import ExportTarget, FileTranscript
from .transcriber import Word

MAX_CUE_S = 7.0  # a cue stays on screen for at most this long
MAX_LINE_CHARS = 42  # the usual subtitle line width
MAX_LINES = 2
MAX_CUE_CHARS = MAX_LINE_CHARS * MAX_LINES
PAUSE_S = 1.0  # a silence this long between two words ends the cue
SENTENCE_END = (".", "!", "?", "…")
MEETING_NAMES = {1: "Me", 2: "Others"}


def _speaker_label(transcript: FileTranscript, speaker: int) -> str:
    if transcript.kind == "meeting" and speaker in MEETING_NAMES:
        return MEETING_NAMES[speaker]
    return f"Speaker {speaker}"


def _wrap(text: str) -> str:
    """Break *text* into at most MAX_LINES lines of about equal length (a single long word stays whole)."""
    if len(text) <= MAX_LINE_CHARS:
        return text
    words = text.split()
    best = text
    best_gap = len(text)
    for i in range(1, len(words)):
        first, second = " ".join(words[:i]), " ".join(words[i:])
        gap = abs(len(first) - len(second))
        if max(len(first), len(second)) <= MAX_LINE_CHARS and gap < best_gap:
            best, best_gap = f"{first}\n{second}", gap
    if "\n" in best:
        return best
    # No split fits both lines: break at the last space that fits the first line.
    cut = text.rfind(" ", 0, MAX_LINE_CHARS + 1)
    return text if cut <= 0 else f"{text[:cut]}\n{text[cut + 1 :]}"


def build_cues(transcript: FileTranscript) -> list[tuple[float, float, str]]:
    """`(start, end, text)` per cue, in seconds.

    A cue ends at a speaker change, a pause of PAUSE_S, the end of a sentence once it is long enough, or when the
    next word would push it past MAX_CUE_S or MAX_CUE_CHARS. The first cue of each speaker turn starts with the
    speaker's label.
    """
    words = [w for w in transcript.words if w.word.strip()]
    cues: list[tuple[float, float, str]] = []
    current: list[Word] = []
    previous_speaker: int | None = None

    def flush() -> None:
        if not current:
            return
        text = " ".join(w.word.strip() for w in current)
        speaker = current[0].speaker
        if speaker is not None and speaker != previous_speaker:
            text = f"{_speaker_label(transcript, speaker)}: {text}"
        start, end = current[0].start, max(current[-1].end, current[0].start)
        cues.append((start, end, _wrap(text)))
        current.clear()

    for word in words:
        if current:
            joined = " ".join(w.word.strip() for w in current) + " " + word.word.strip()
            label_room = len(_speaker_label(transcript, word.speaker or 0)) + 2 if word.speaker else 0
            if (
                word.speaker != current[-1].speaker
                or word.start - current[-1].end >= PAUSE_S
                or word.end - current[0].start > MAX_CUE_S
                or len(joined) + label_room > MAX_CUE_CHARS
                or (current[-1].word.strip().endswith(SENTENCE_END) and len(joined) > MAX_LINE_CHARS // 2)
            ):
                speaker = current[0].speaker
                flush()
                previous_speaker = speaker
        current.append(word)
    flush()

    # Overlapping cues make players flicker: a cue never runs into the next one.
    for i in range(len(cues) - 1):
        start, end, text = cues[i]
        if end > cues[i + 1][0]:
            cues[i] = (start, max(start, cues[i + 1][0]), text)
    return cues


def _timestamp(seconds: float, separator: str) -> str:
    total_ms = round(max(seconds, 0.0) * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{ms:03d}"


def _require_words(transcript: FileTranscript) -> None:
    if not any(w.word.strip() for w in transcript.words):
        raise ValueError("This upload has no word timestamps (use Parakeet to get them), so no subtitles can be made")


def to_txt(transcript: FileTranscript) -> str:
    """The transcript as shown: cleaned when cleanup ran, with `Speaker N:` paragraphs when speakers were detected."""
    return transcript.best_text.rstrip() + "\n"


def to_srt(transcript: FileTranscript) -> str:
    _require_words(transcript)
    blocks = [
        f"{n}\n{_timestamp(start, ',')} --> {_timestamp(end, ',')}\n{text}"
        for n, (start, end, text) in enumerate(build_cues(transcript), 1)
    ]
    return "\n\n".join(blocks) + "\n"


def to_vtt(transcript: FileTranscript) -> str:
    _require_words(transcript)
    blocks = [
        f"{_timestamp(start, '.')} --> {_timestamp(end, '.')}\n{text.replace('&', '&amp;').replace('<', '&lt;')}"
        for start, end, text in build_cues(transcript)
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def builtin_export_targets() -> list[ExportTarget]:
    return [
        ExportTarget("txt", "TXT", ".txt", "text/plain; charset=utf-8", to_txt),
        ExportTarget("srt", "SRT", ".srt", "application/x-subrip; charset=utf-8", to_srt),
        ExportTarget("vtt", "VTT", ".vtt", "text/vtt; charset=utf-8", to_vtt),
    ]
