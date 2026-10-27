"""Turn a recorded meeting folder into one Me/Others transcript and a playable mix.

The recorder (`meeting_recorder.py`) leaves `mic.wav` (Me), `system.wav` (Others) and `meeting.json`.
This module loads that folder (repairing the WAV headers of a recording that was killed), converts each
track to the 16 kHz mono 60 s chunks the upload pipeline takes, merges both tracks' timed words into
speaker turns and mixes `meeting.wav`. Transcription itself stays in `file_transcribe.transcribe_file`.

Memory: audio is read, resampled and mixed one 60 s window at a time. The one thing held in full is the WAV
chunks of the track being transcribed (`transcribe_file` takes a list), roughly 115 MB per hour of meeting.
"""

from __future__ import annotations

import bisect
import difflib
import json
import logging
import os
import re
import struct
import time
import wave
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .diarization import format_transcript
from .file_transcribe import MAX_CHUNK_MS
from .meeting_recorder import META_FILENAME
from .transcriber import Word

logger = logging.getLogger(__name__)

TARGET_RATE = 16_000
MIX_FILENAME = "meeting.wav"
TRACK_NAMES = {"mic": "Me", "system": "Others"}
# Speaker numbers stored on the words, in the same format speaker detection uses for uploads.
SPEAKER_ME = 1
SPEAKER_OTHERS = 2
SPEAKER_LABELS = {SPEAKER_ME: "Me", SPEAKER_OTHERS: "Others"}

# A track file written to this recently may still be recorded: its header is not touched.
STALE_AFTER_S = 30.0

# Mix balance: each track's speech is brought to this RMS (about -20 dBFS). A gain cap keeps a track that
# is nearly silent (a muted device, room noise only) from being boosted into noise.
TARGET_RMS = 3000.0
NOISE_FLOOR_RMS = 100.0
MAX_GAIN = 8.0
MIN_GAIN = 0.1
PEAK_LIMIT = 32000.0

# Echo filter: a Me word is the speakers' sound coming back through the microphone when an Others word
# sounds like it and sits at the same time (the delay through the room and the engines' timing noise). One
# word proves nothing - "the"/"they", or both people saying "okay" - so only a run of at least
# `ECHO_MIN_RUN` consecutive Me words that all have such a partner counts as echo.
ECHO_SLACK_S = 0.6
ECHO_SIMILARITY = 0.8
ECHO_MIN_RUN = 3
# A Me word within this many seconds of a dictation take still counts as dictated: the first word may be heard
# before the key press registers and the last after the key release.
DICTATION_MARGIN_S = 0.5
_MAX_WORD_S = 3.0  # no word lasts longer; bounds the search window in the Others list


# ---- Loading and repairing the folder ---------------------------------------


@dataclass
class MeetingTrack:
    name: str  # "mic" or "system"
    path: Path
    sample_rate: int
    channels: int
    frames: int


@dataclass
class Meeting:
    folder: Path
    started_at: str | None
    interrupted: bool  # `ended_at` is null: the process died before `stop()`
    tracks: dict[str, MeetingTrack] = field(default_factory=dict)
    # (start, end) seconds from the meeting start of every dictation take, as stored by the recorder.
    dictations: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def repair_wav_header(path: Path, *, stale_after_s: float = STALE_AFTER_S) -> bool:
    """Rewrite the RIFF and data chunk sizes of a WAV whose header does not match the file; True when changed.

    The recorder writes its headers when it stops, so a killed process leaves a length of zero. The audio
    is the data chunk up to the end of the file (cut to whole frames). A file is only opened for writing
    when its header is wrong, and then only if nothing wrote to it for `stale_after_s` seconds: a recording
    that is still running has the same header and must not be touched.
    """
    with path.open("rb") as f:
        fix = _find_header_fix(f)
    if fix is None:
        return False
    if time.time() - path.stat().st_mtime < stale_after_s:
        raise ValueError(f"{path.name} was written to a moment ago; the meeting may still be recording")
    data_size_pos, data_size, end = fix
    with path.open("r+b") as f:
        f.seek(data_size_pos)
        f.write(struct.pack("<I", data_size))
        f.truncate(end)
        f.seek(4)
        f.write(struct.pack("<I", end - 8))
    return True


def _find_header_fix(f) -> tuple[int, int, int] | None:
    """(position of the data size field, right data size, right file size), or None when the header is right."""
    head = f.read(12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    size = f.seek(0, 2)
    pos = 12
    block_align = 0
    while pos + 8 <= size:
        f.seek(pos)
        chunk_id, chunk_size = struct.unpack("<4sI", f.read(8))
        body = pos + 8
        if chunk_id == b"fmt ":
            f.seek(body + 12)
            block_align = struct.unpack("<H", f.read(2))[0]
        elif chunk_id == b"data":
            if not block_align:
                raise ValueError("no fmt chunk before the data")
            available = size - body
            f.seek(4)
            declared_riff = struct.unpack("<I", f.read(4))[0]
            # A declared length that fits the file is trusted (a valid file, maybe with trailing chunks);
            # zero or too long means the header was never patched.
            if 0 < chunk_size <= available:
                return None if declared_riff == size - 8 else (pos + 4, chunk_size, size)
            data_size = available - available % block_align
            return pos + 4, data_size, body + data_size
        pos = body + chunk_size + (chunk_size & 1)
    raise ValueError("no data chunk")


def _parse_dictations(raw) -> list[tuple[float, float]]:
    """The `dictations` list of `meeting.json` as sorted (start, end) pairs; entries that are not usable are skipped."""
    ranges: list[tuple[float, float]] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            start, end = float(item["start_s"]), float(item["end_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if end >= start:
            ranges.append((start, end))
    return sorted(ranges)


def load_meeting(folder: str | Path) -> Meeting:
    """Read `meeting.json` and open both tracks, repairing headers first.

    A folder whose `ended_at` is null is treated as interrupted; its tracks are still used, unless they were
    written to a moment ago (then the meeting may still be recording and loading it raises). A track that is
    missing or unreadable is left out with a note.
    """
    folder = Path(folder)
    meta_path = folder / META_FILENAME
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{folder.name} has no readable {META_FILENAME}: {exc}") from exc
    meeting = Meeting(folder, meta.get("started_at"), interrupted=meta.get("ended_at") is None)
    if meeting.interrupted:
        meeting.notes.append(
            "The recording was interrupted (the app stopped before the meeting ended); it was recovered."
        )
    meeting.dictations = _parse_dictations(meta.get("dictations"))
    for name, info in (meta.get("tracks") or {}).items():
        if name not in TRACK_NAMES:
            continue
        label = TRACK_NAMES[name]
        if info.get("error"):
            meeting.notes.append(f"The {label} track reported an error while recording: {info['error']}")
        path = folder / str(info.get("file") or f"{name}.wav")
        try:
            if repair_wav_header(path):
                meeting.notes.append(f"The {label} track's header was repaired from the file size.")
            with wave.open(str(path), "rb") as wf:
                if wf.getsampwidth() != 2:
                    raise ValueError("not PCM16")
                meeting.tracks[name] = MeetingTrack(name, path, wf.getframerate(), wf.getnchannels(), wf.getnframes())
        except (OSError, ValueError, wave.Error, EOFError) as exc:
            if meeting.interrupted and "may still be recording" in str(exc):
                raise ValueError(f"{folder.name} may still be recording: {exc}") from exc
            meeting.notes.append(f"The {label} track could not be read and is left out: {exc}")
    return meeting


# ---- Audio ------------------------------------------------------------------


def _wav_bytes(pcm: np.ndarray) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def iter_track_chunks(track: MeetingTrack) -> Iterator[np.ndarray]:
    """The track as 16 kHz mono int16 arrays of 60 s each (the last one shorter), read window by window.

    Every window is resampled from the track's own device rate, so two devices whose clocks do not run at
    exactly their nominal rates still end up on one 16 kHz timeline, and memory stays at one window.
    """
    window = track.sample_rate * (MAX_CHUNK_MS // 1000)
    with wave.open(str(track.path), "rb") as wf:
        while raw := wf.readframes(window):
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
            if track.channels > 1:
                samples = samples[: len(samples) // track.channels * track.channels]
                samples = samples.reshape(-1, track.channels).mean(axis=1)
            yield _resample(samples, track.sample_rate)


def _resample(samples: np.ndarray, rate: int) -> np.ndarray:
    if len(samples) == 0:
        return np.zeros(0, dtype=np.int16)
    if rate != TARGET_RATE:
        out_len = max(1, round(len(samples) * TARGET_RATE / rate))
        samples = np.interp(np.linspace(0, len(samples) - 1, out_len), np.arange(len(samples)), samples)
    return np.clip(np.rint(samples), -32768, 32767).astype(np.int16)


def convert_track(track: MeetingTrack, out_path: Path) -> list[bytes]:
    """The 60 s 16 kHz mono WAV chunks `transcribe_file` takes; the same audio is also written to `out_path`
    (one 16 kHz mono WAV) for the mix, so the arrays never have to be kept."""
    chunks: list[bytes] = []
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        for array in iter_track_chunks(track):
            chunks.append(_wav_bytes(array))
            wf.writeframes(array.tobytes())
    return chunks


def _read_windows(path: Path) -> Iterator[np.ndarray]:
    window = TARGET_RATE * (MAX_CHUNK_MS // 1000)
    with wave.open(str(path), "rb") as wf:
        while raw := wf.readframes(window):
            yield np.frombuffer(raw, dtype="<i2")


def speech_rms(chunks: Iterable[np.ndarray]) -> float:
    """RMS of the track's speech: the 50 ms blocks within 20 dB of its loud blocks and above the noise floor.

    Silence and room noise between sentences would otherwise pull the level down and make a quiet,
    mostly idle track look quieter than it sounds when someone talks. 0 when nothing is audible.
    """
    return _analyse(chunks)[0]


def _analyse(chunks: Iterable[np.ndarray]) -> tuple[float, int]:
    """(speech RMS, peak amplitude) of the track."""
    block = TARGET_RATE // 20
    levels: list[float] = []
    peak = 0
    for chunk in chunks:
        if len(chunk):
            peak = max(peak, int(np.abs(chunk.astype(np.int32)).max()))
        n = len(chunk) // block
        if n:
            blocks = chunk[: n * block].astype(np.float32).reshape(n, block)
            levels.extend(np.mean(blocks**2, axis=1).tolist())
    if not levels:
        return 0.0, peak
    power = np.asarray(levels)
    loud = float(np.percentile(power, 95))
    active = power[(power >= loud * 0.01) & (power >= NOISE_FLOOR_RMS**2)]
    return (float(np.sqrt(active.mean())) if len(active) else 0.0), peak


def _gain_for(level: float) -> float:
    if level <= 0:
        return 1.0
    return float(min(max(TARGET_RMS / level, MIN_GAIN), MAX_GAIN))


def balance_gain(chunks: Iterable[np.ndarray]) -> float:
    """The factor that brings the track's speech to `TARGET_RMS`, capped so a near-silent track stays quiet."""
    return _gain_for(speech_rms(chunks))


def mix_tracks(paths: list[Path], out_path: Path) -> float:
    """Mix 16 kHz mono WAV tracks into one mono WAV at `out_path`; returns its length in seconds.

    Each track is first brought to the same speech level (`balance_gain`), so a loud system track cannot
    drown the microphone. The sum is scaled so that even coinciding peaks stay under `PEAK_LIMIT`: the bound
    is the sum of the tracks' gained peaks, known from the analysis pass, so the mix itself is a single pass.
    The tracks start together; the shorter one is padded with silence. The inputs are not changed. The mix is
    written to a temporary file and moved into place, so a failure leaves no partial `out_path`.
    """
    gains, bound = [], 0.0
    for path in paths:
        level, peak = _analyse(_read_windows(path))
        gain = _gain_for(level)
        gains.append(gain)
        bound += gain * peak
    scale = min(1.0, PEAK_LIMIT / bound) if bound else 1.0
    tmp = out_path.with_name(out_path.name + ".tmp")
    frames = 0
    try:
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_RATE)
            readers = [_read_windows(p) for p in paths]
            while True:
                parts = [(w, g) for r, g in zip(readers, gains, strict=True) if (w := next(r, None)) is not None]
                if not parts:
                    break
                mix = np.zeros(max(len(p) for p, _ in parts), dtype=np.float32)
                for part, gain in parts:
                    mix[: len(part)] += part.astype(np.float32) * (gain * scale)
                wf.writeframes(np.rint(mix).astype("<i2").tobytes())
                frames += len(mix)
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return frames / TARGET_RATE


# ---- Merging ----------------------------------------------------------------


def _norm(word: str) -> str:
    return re.sub(r"\W+", "", word.casefold())


def drop_echo(me: list[Word], others: list[Word]) -> list[Word]:
    """The Me words minus the runs the microphone picked up from the speakers (heard in Others at the same time).

    A Me word has an echo partner when an Others word within `ECHO_SLACK_S` of it is `ECHO_SIMILARITY`
    similar; only runs of `ECHO_MIN_RUN` consecutive Me words that all have one are dropped, so a short
    "yes" or "okay" said at once, or "the"/"they" in crosstalk, stays.
    """
    others = sorted(others, key=lambda w: w.start)
    starts = [o.start for o in others]
    norms = [_norm(o.word) for o in others]
    partner = [False] * len(me)
    for i, word in enumerate(me):
        text = _norm(word.word)
        if not text:
            continue
        j = bisect.bisect_left(starts, word.start - ECHO_SLACK_S - _MAX_WORD_S)
        while j < len(others) and starts[j] <= word.end + ECHO_SLACK_S:
            other = others[j]
            if (
                other.end >= word.start - ECHO_SLACK_S
                and norms[j]
                and difflib.SequenceMatcher(None, text, norms[j]).ratio() >= ECHO_SIMILARITY
            ):
                partner[i] = True
                break
            j += 1
    drop = [False] * len(me)
    i = 0
    while i < len(me):
        if not partner[i]:
            i += 1
            continue
        j = i
        while j < len(me) and partner[j]:
            j += 1
        if j - i >= ECHO_MIN_RUN:
            drop[i:j] = [True] * (j - i)
        i = j
    return [w for w, d in zip(me, drop, strict=True) if not d]


def drop_dictation(
    me: list[Word], dictations: list[tuple[float, float]], margin: float = DICTATION_MARGIN_S
) -> list[Word]:
    """The Me words minus those that touch a dictation take (padded by `margin` seconds on both sides).

    The microphone records what the user dictates; that text was typed elsewhere, so it does not belong in the
    meeting. A word is dropped when its time span overlaps a padded range.
    `dictations` must be sorted by start, as `load_meeting` returns it.
    """
    ranges = [(start - margin, end + margin) for start, end in dictations]  # sorted by start, as stored
    starts = [r[0] for r in ranges]
    # Prefix maximum of the range ends, so the search below can stop at the first range that cannot reach the word.
    reach: list[float] = []
    for _, end in ranges:
        reach.append(max(end, reach[-1]) if reach else end)
    kept = []
    for word in me:
        i = bisect.bisect_right(starts, word.end) - 1  # last range that starts before the word ends
        if i >= 0 and reach[i] >= word.start:
            continue
        kept.append(word)
    return kept


def merge_words(me: list[Word], others: list[Word], dictations: list[tuple[float, float]] | None = None) -> list[Word]:
    """Both tracks' words in time order, each labelled Me or Others, with the echo removed from Me.

    `dictations` (time ranges of dictation takes) removes the Me words spoken into the dictation; None keeps them.
    """
    others = sorted(others, key=lambda w: (w.start, w.end))
    me = drop_echo(sorted(me, key=lambda w: (w.start, w.end)), others)
    if dictations:
        # After the echo filter: taking dictated words out first could join two short echo runs into one.
        me = drop_dictation(me, dictations)
    merged = [Word(w.word, w.start, w.end, SPEAKER_ME) for w in me]
    merged += [Word(w.word, w.start, w.end, SPEAKER_OTHERS) for w in others]
    merged.sort(key=lambda w: (w.start, w.end, w.speaker))
    return merged


def format_meeting(words: list[Word]) -> str:
    """ "Me: ..." / "Others: ..." paragraphs, one per speaker turn."""
    return format_transcript(words, SPEAKER_LABELS)
