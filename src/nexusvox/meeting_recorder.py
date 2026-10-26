"""Meeting recorder: the default microphone and the system output, each streamed to its own WAV file.

Independent of dictation (`audio.py`): it opens its own streams, at each device's native sample rate, and
never touches the 16 kHz dictation path. Audio goes to disk in chunks as it arrives, so memory stays flat
however long the meeting runs.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

SAMPLE_WIDTH = 2  # PCM16
# A gap shorter than this is device jitter, not silence; it is not padded.
GAP_TOLERANCE_S = 0.5
# Padding is written in slices of at most this many seconds, so a long silence never allocates a long buffer.
PAD_SLICE_S = 1.0
QUEUE_CHUNKS = 4096
META_FILENAME = "meeting.json"


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int


class AudioSource(Protocol):
    """A capture device that delivers PCM16 bytes from its own audio thread."""

    def describe(self) -> AudioFormat:
        """The device's native format, without opening a stream."""

    def start(self, sink: Callable[[bytes], None]) -> None:
        """Open the stream; call `sink(pcm16_bytes)` for every block."""

    def stop(self) -> None: ...


class MicSource:
    """The default input device through sounddevice, mono, at the device's native rate."""

    def __init__(self) -> None:
        self._stream = None

    def describe(self) -> AudioFormat:
        import sounddevice as sd

        return AudioFormat(int(sd.query_devices(kind="input")["default_samplerate"]), 1)

    def start(self, sink: Callable[[bytes], None]) -> None:
        import sounddevice as sd

        fmt = self.describe()

        def callback(indata, frames, time_info, status) -> None:
            if status:
                logger.warning("Meeting mic status: %s", status)
            sink(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=fmt.sample_rate, channels=fmt.channels, dtype="int16", callback=callback
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class LoopbackSource:
    """The default output device in WASAPI loopback mode through PyAudioWPatch (Windows only).

    Loopback delivers no frames while nothing plays; `TrackWriter` pads that silence.
    """

    def __init__(self) -> None:
        self._pa = None
        self._stream = None

    @staticmethod
    def _find_device(pa, pyaudio) -> dict:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for loopback in pa.get_loopback_device_info_generator():
            if speakers["name"] in loopback["name"]:
                return loopback
        raise RuntimeError(f"No loopback device for output {speakers['name']!r}")

    def describe(self) -> AudioFormat:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            dev = self._find_device(pa, pyaudio)
            return AudioFormat(int(dev["defaultSampleRate"]), int(dev["maxInputChannels"]))
        finally:
            pa.terminate()

    def start(self, sink: Callable[[bytes], None]) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        try:
            dev = self._find_device(self._pa, pyaudio)

            def callback(in_data, frame_count, time_info, status):
                sink(in_data)
                return None, pyaudio.paContinue

            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=int(dev["maxInputChannels"]),
                rate=int(dev["defaultSampleRate"]),
                input=True,
                input_device_index=dev["index"],
                stream_callback=callback,
            )
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None


class TrackWriter:
    """Streams one track to a WAV file and keeps it aligned with the shared start time.

    `write` is told when each block arrived. A block's audio ends at its arrival, so the frames the track
    should hold before it follow from the clock; when the file holds fewer by more than `GAP_TOLERANCE_S`
    (a loopback device that sent nothing during silence), zeros fill the difference.
    """

    def __init__(self, path: Path, fmt: AudioFormat, t0: float) -> None:
        self.path = path
        self.fmt = fmt
        self._t0 = t0
        self.frames = 0
        self._wav = wave.open(str(path), "wb")  # noqa: SIM115 - closed in finish()
        self._wav.setnchannels(fmt.channels)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(fmt.sample_rate)
        self._frame_bytes = SAMPLE_WIDTH * fmt.channels

    def _pad(self, frames: int) -> None:
        slice_frames = max(1, int(PAD_SLICE_S * self.fmt.sample_rate))
        while frames > 0:
            n = min(frames, slice_frames)
            self._wav.writeframes(bytes(n * self._frame_bytes))
            self.frames += n
            frames -= n

    def write(self, data: bytes, arrival: float) -> None:
        block_frames = len(data) // self._frame_bytes
        expected = round((arrival - self._t0) * self.fmt.sample_rate) - block_frames
        deficit = expected - self.frames
        if deficit > GAP_TOLERANCE_S * self.fmt.sample_rate:
            self._pad(deficit)
        self._wav.writeframes(data[: block_frames * self._frame_bytes])
        self.frames += block_frames

    def finish(self, end: float) -> None:
        try:
            self._pad(round((end - self._t0) * self.fmt.sample_rate) - self.frames)
        finally:
            self._wav.close()


class _Track:
    """One source, its writer and the thread that moves blocks from the audio callback to disk."""

    def __init__(self, name: str, source: AudioSource, writer: TrackWriter, clock: Callable[[], float]) -> None:
        self.name = name
        self.source = source
        self.writer = writer
        self._clock = clock
        self._queue: queue.Queue[tuple[float, bytes] | None] = queue.Queue(QUEUE_CHUNKS)
        self._thread = threading.Thread(target=self._run, name=f"meeting-{name}", daemon=True)
        self.dropped = 0
        self.error: str | None = None

    def sink(self, data: bytes) -> None:
        """Runs on the audio thread: never blocks, a full queue drops the block."""
        try:
            self._queue.put_nowait((self._clock(), data))
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            if self.error is None:
                try:
                    self.writer.write(item[1], item[0])
                except Exception as exc:  # disk full, file removed: keep draining, report at the end
                    self.error = str(exc)
                    logger.error("Meeting %s track failed: %s", self.name, exc)
            self._queue.task_done()
        self._queue.task_done()

    def begin(self) -> None:
        self._thread.start()

    def flush(self) -> None:
        self._queue.join()

    def end(self) -> None:
        self._queue.put(None)
        self._thread.join()


class MeetingRecorder:
    """Start/stop recorder writing `mic.wav`, `system.wav` and `meeting.json` into one folder per meeting."""

    def __init__(
        self,
        base_dir: str | Path,
        mic: AudioSource | None = None,
        system: AudioSource | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._base_dir = Path(base_dir)
        self._sources: dict[str, AudioSource] = {"mic": mic or MicSource(), "system": system or LoopbackSource()}
        self._clock = clock
        self._now = now
        self._tracks: dict[str, _Track] = {}
        self._started_at: datetime | None = None
        self.folder: Path | None = None

    @property
    def recording(self) -> bool:
        return bool(self._tracks)

    def _new_folder(self, started_at: datetime) -> Path:
        stem = started_at.astimezone().strftime("meeting-%Y%m%d-%H%M%S")
        folder = self._base_dir / stem
        n = 1
        while folder.exists():
            n += 1
            folder = self._base_dir / f"{stem}-{n}"
        folder.mkdir(parents=True)
        return folder

    def start(self) -> Path:
        if self.recording:
            raise RuntimeError("Meeting recording already running")
        self._started_at = self._now()
        t0 = self._clock()
        folder = self._new_folder(self._started_at)
        tracks: dict[str, _Track] = {}
        try:
            for name, source in self._sources.items():
                writer = TrackWriter(folder / f"{name}.wav", source.describe(), t0)
                tracks[name] = _Track(name, source, writer, self._clock)
            self._tracks = tracks
            self.folder = folder
            self._write_meta(ended_at=None)
            for track in tracks.values():
                track.begin()
            for track in tracks.values():
                track.source.start(track.sink)
        except Exception:
            self._abort(tracks)
            raise
        logger.info("Meeting recording started: %s", folder)
        return folder

    def _abort(self, tracks: dict[str, _Track]) -> None:
        self._tracks = {}
        for track in tracks.values():
            try:
                track.source.stop()
            except Exception:
                logger.exception("Stopping meeting source %s failed", track.name)
            if track._thread.is_alive():
                track.end()
            track.writer.finish(self._clock())

    def flush(self) -> None:
        """Wait until every block received so far is on disk."""
        for track in self._tracks.values():
            track.flush()

    def stop(self) -> Path:
        if not self.recording:
            raise RuntimeError("No meeting recording running")
        tracks = self._tracks
        for track in tracks.values():
            try:
                track.source.stop()
            except Exception:
                logger.exception("Stopping meeting source %s failed", track.name)
        end = self._clock()
        for track in tracks.values():
            track.end()
            track.writer.finish(end)
        self._tracks = {}
        self._write_meta(ended_at=self._now(), tracks=tracks)
        logger.info("Meeting recording stopped: %s", self.folder)
        assert self.folder is not None
        return self.folder

    def _write_meta(self, *, ended_at: datetime | None, tracks: dict[str, _Track] | None = None) -> None:
        assert self.folder is not None and self._started_at is not None
        info = {}
        for name, track in (tracks or self._tracks).items():
            fmt = track.writer.fmt
            entry = {
                "file": track.writer.path.name,
                "sample_rate": fmt.sample_rate,
                "channels": fmt.channels,
                "sample_width": SAMPLE_WIDTH,
            }
            if tracks is not None:
                entry["frames"] = track.writer.frames
                entry["duration_s"] = round(track.writer.frames / fmt.sample_rate, 3)
                if track.dropped:
                    entry["dropped_blocks"] = track.dropped
                if track.error:
                    entry["error"] = track.error
            info[name] = entry
        meta = {
            "version": 1,
            "started_at": self._started_at.isoformat(),
            "ended_at": ended_at.isoformat() if ended_at else None,
            "tracks": info,
        }
        tmp = self.folder / (META_FILENAME + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp, self.folder / META_FILENAME)


def main(argv: list[str] | None = None) -> None:
    """Manual check: `python -m nexusvox.meeting_recorder --seconds 120`."""
    import argparse

    parser = argparse.ArgumentParser(description="Record the microphone and the system audio to two WAV files.")
    parser.add_argument("--dir", default="audio/meetings", help="folder that receives one subfolder per meeting")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this long (default: until Ctrl+C)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    recorder = MeetingRecorder(args.dir)
    print(f"Recording to {recorder.start()} - Ctrl+C to stop")
    try:
        time.sleep(args.seconds) if args.seconds > 0 else threading.Event().wait()
    except KeyboardInterrupt:
        pass
    print(f"Saved {recorder.stop()}")


if __name__ == "__main__":
    main()
