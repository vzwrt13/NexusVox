"""Turn a recorded meeting folder into one Me/Others transcript and a playable mix.

The recorder (`meeting_recorder.py`) leaves `mic.wav` (Me), `system.wav` (Others) and `meeting.json`.
This module loads that folder (repairing the WAV headers of a recording that was killed), converts each
track to the 16 kHz mono 60 s chunks the upload pipeline takes, merges both tracks' timed words into
speaker turns and mixes `meeting.wav`. Transcription itself stays in `file_transcribe.transcribe_file`.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import struct
import wave
from collections.abc import Iterator
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

# Mix balance: each track's speech is brought to this RMS (about -20 dBFS). A gain cap keeps a track that
# is nearly silent (a muted device, room noise only) from being boosted into noise.
TARGET_RMS = 3000.0
NOISE_FLOOR_RMS = 100.0
MAX_GAIN = 8.0
MIN_GAIN = 0.1
PEAK_LIMIT = 32000.0

# Echo filter: a Me word is the speakers' sound coming back through the microphone when an Others word
# sounds like it and sits at the same time (the delay through the room and the engines' timing noise).
ECHO_SLACK_S = 0.6
ECHO_SIMILARITY = 0.75


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
    notes: list[str] = field(default_factory=list)


def repair_wav_header(path: Path) -> bool:
    """Rewrite the RIFF and data chunk sizes of a WAV whose header does not match the file; True when changed.

    The recorder writes its headers when it stops, so a killed process leaves a length of zero. The audio
    is the data chunk up to the end of the file (cut to whole frames); the chunks after it, if any, are
    not expected from this recorder and are treated as part of the audio only when the data chunk is last.
    """
    with path.open("r+b") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"{path.name} is not a WAV file")
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
                    raise ValueError(f"{path.name} has no fmt chunk before its data")
                available = size - body
                # A declared length that still fits the file is trusted (a valid file, maybe with trailing chunks);
                # zero or too long means the header was never patched.
                if 0 < chunk_size <= available:
                    return _fix_riff_size(f, size)
                data_size = available - available % block_align
                f.seek(pos + 4)
                f.write(struct.pack("<I", data_size))
                f.truncate(body + data_size)
                _fix_riff_size(f, body + data_size)
                return True
            pos = body + chunk_size + (chunk_size & 1)
        raise ValueError(f"{path.name} has no data chunk")


def _fix_riff_size(f, file_size: int) -> bool:
    f.seek(4)
    declared = struct.unpack("<I", f.read(4))[0]
    if declared == file_size - 8:
        return False
    f.seek(4)
    f.write(struct.pack("<I", file_size - 8))
    return True


def load_meeting(folder: str | Path) -> Meeting:
    """Read `meeting.json` and open both tracks, repairing headers first.

    A folder whose `ended_at` is null is treated as interrupted; its tracks are still used. A track
    that is missing or unreadable is left out with a note.
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


def track_to_wav_chunks(track: MeetingTrack) -> tuple[list[bytes], list[np.ndarray]]:
    """The 60 s WAV chunks `transcribe_file` takes, and the same audio as arrays for mixing."""
    arrays = list(iter_track_chunks(track))
    return [_wav_bytes(a) for a in arrays], arrays


def speech_rms(chunks: list[np.ndarray]) -> float:
    """RMS of the track's speech: the 50 ms blocks within 20 dB of its loud blocks and above the noise floor.

    Silence and room noise between sentences would otherwise pull the level down and make a quiet,
    mostly idle track look quieter than it sounds when someone talks. 0 when nothing is audible.
    """
    block = TARGET_RATE // 20
    levels: list[float] = []
    for chunk in chunks:
        n = len(chunk) // block
        if n:
            blocks = chunk[: n * block].astype(np.float32).reshape(n, block)
            levels.extend(np.mean(blocks**2, axis=1).tolist())
    if not levels:
        return 0.0
    power = np.asarray(levels)
    loud = float(np.percentile(power, 95))
    active = power[(power >= loud * 0.01) & (power >= NOISE_FLOOR_RMS**2)]
    return float(np.sqrt(active.mean())) if len(active) else 0.0


def balance_gain(chunks: list[np.ndarray]) -> float:
    """The factor that brings the track's speech to `TARGET_RMS`, capped so a near-silent track stays quiet."""
    level = speech_rms(chunks)
    if level <= 0:
        return 1.0
    return float(min(max(TARGET_RMS / level, MIN_GAIN), MAX_GAIN))


def mix_tracks(tracks: list[list[np.ndarray]], out_path: Path) -> float:
    """Mix the tracks' 16 kHz chunks into one mono WAV at `out_path`; returns its length in seconds.

    Each track is brought to the same speech level first (`balance_gain`), so a loud system track cannot
    drown the microphone; the sum is scaled down as a whole when it would clip. The tracks share a start,
    so chunk i of each lines up; the shorter track is padded with silence. The inputs are not changed.
    """
    gains = [balance_gain(t) for t in tracks]

    def mixed() -> Iterator[np.ndarray]:
        for i in range(max((len(t) for t in tracks), default=0)):
            parts = [(t[i], g) for t, g in zip(tracks, gains, strict=True) if i < len(t)]
            mix = np.zeros(max(len(p) for p, _ in parts), dtype=np.float32)
            for part, gain in parts:
                mix[: len(part)] += part.astype(np.float32) * gain
            yield mix

    peak = max((float(np.abs(m).max()) for m in mixed() if len(m)), default=0.0)
    scale = min(1.0, PEAK_LIMIT / peak) if peak else 1.0
    frames = 0
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        for mix in mixed():
            wf.writeframes(np.rint(mix * scale).astype("<i2").tobytes())
            frames += len(mix)
    return frames / TARGET_RATE


# ---- Merging ----------------------------------------------------------------


def _norm(word: str) -> str:
    return re.sub(r"\W+", "", word.casefold())


def drop_echo(me: list[Word], others: list[Word]) -> list[Word]:
    """The Me words minus those the microphone picked up from the speakers (heard in Others at the same time)."""
    kept: list[Word] = []
    pool = [(o, _norm(o.word)) for o in others]
    for word in me:
        text = _norm(word.word)
        echo = False
        if text:
            for other, other_text in pool:
                if other.end < word.start - ECHO_SLACK_S:
                    continue
                if other.start > word.end + ECHO_SLACK_S:
                    break  # others are in time order
                if other_text and difflib.SequenceMatcher(None, text, other_text).ratio() >= ECHO_SIMILARITY:
                    echo = True
                    break
        if not echo:
            kept.append(word)
    return kept


def merge_words(me: list[Word], others: list[Word]) -> list[Word]:
    """Both tracks' words in time order, each labelled Me or Others, with the echo removed from Me."""
    others = sorted(others, key=lambda w: (w.start, w.end))
    me = drop_echo(sorted(me, key=lambda w: (w.start, w.end)), others)
    merged = [Word(w.word, w.start, w.end, SPEAKER_ME) for w in me]
    merged += [Word(w.word, w.start, w.end, SPEAKER_OTHERS) for w in others]
    merged.sort(key=lambda w: (w.start, w.end, w.speaker))
    return merged


def format_meeting(words: list[Word]) -> str:
    """ "Me: ..." / "Others: ..." paragraphs, one per speaker turn."""
    return format_transcript(words, SPEAKER_LABELS)
