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
import shutil
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import audio_state

logger = logging.getLogger(__name__)

SAMPLE_WIDTH = 2  # PCM16
# A gap shorter than this is device jitter, not silence; it is not padded.
GAP_TOLERANCE_S = 0.25
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


_refresh_lock = threading.Lock()


def _refresh_devices(sd) -> None:
    """Make PortAudio notice devices plugged in since it started: it lists them once, at initialisation.

    Re-initialising invalidates open streams, so it only happens while none is open here or in dictation.
    """
    with _refresh_lock:
        if audio_state.streams_open():
            return
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            logger.exception("Refreshing the audio device list failed")


def _wasapi_index(sd) -> int | None:
    """Index of the Windows WASAPI host API, whose names are the full ones (MME cuts them at 31 characters)."""
    for index, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return index
    return None


def list_devices() -> dict[str, list[str]]:
    """Names of the input and the output devices a meeting can use: `{"inputs": [...], "outputs": [...]}`.

    A device that cannot be listed (no audio stack, no PyAudioWPatch) leaves its list empty.
    """
    inputs: list[str] = []
    outputs: list[str] = []
    try:
        import sounddevice as sd

        _refresh_devices(sd)
        wasapi = _wasapi_index(sd)
        for dev in sd.query_devices():
            if dev["max_input_channels"] > 0 and (wasapi is None or dev["hostapi"] == wasapi):
                if dev["name"] not in inputs:
                    inputs.append(dev["name"])
    except Exception:
        logger.exception("Listing the input devices failed")
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(wasapi_info["deviceCount"]):
                dev = pa.get_device_info_by_host_api_device_index(wasapi_info["index"], i)
                if dev["maxOutputChannels"] > 0 and not dev.get("isLoopbackDevice") and dev["name"] not in outputs:
                    outputs.append(dev["name"])
        finally:
            pa.terminate()
    except Exception:
        logger.exception("Listing the output devices failed")
    return {"inputs": inputs, "outputs": outputs}


class MicSource:
    """An input device through sounddevice (the default one, or the one named `device`), mono, at its native rate.

    `describe()` pins the device and its rate; `start()` opens exactly that, so the WAV header written
    from `describe()` always matches the stream even if the default input changes in between.
    """

    def __init__(self, device: str = "") -> None:
        self._name = device
        self._stream = None
        self._device: int | None = None
        self._fmt: AudioFormat | None = None

    def _find(self, sd) -> dict:
        """The named input device (on WASAPI when there is one); the default input when unnamed or gone."""
        if self._name:
            _refresh_devices(sd)
            wasapi = _wasapi_index(sd)
            for dev in sd.query_devices():
                if dev["name"] == self._name and dev["max_input_channels"] > 0:
                    if wasapi is None or dev["hostapi"] == wasapi:
                        return dev
            logger.warning("Meeting microphone %r not found; using the default input", self._name)
        return sd.query_devices(kind="input")

    def describe(self) -> AudioFormat:
        import sounddevice as sd

        dev = self._find(sd)
        self._device = int(dev["index"])
        self._fmt = AudioFormat(int(dev["default_samplerate"]), 1)
        return self._fmt

    def start(self, sink: Callable[[bytes], None]) -> None:
        import sounddevice as sd

        fmt = self._fmt or self.describe()

        def callback(indata, frames, time_info, status) -> None:
            if status:
                logger.warning("Meeting mic status: %s", status)
            sink(bytes(indata))

        self._stream = sd.RawInputStream(
            device=self._device, samplerate=fmt.sample_rate, channels=fmt.channels, dtype="int16", callback=callback
        )
        self._stream.start()
        audio_state.stream_opened()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            audio_state.stream_closed()


class LoopbackSource:
    """An output device (the default one, or the one named `output`) in WASAPI loopback mode through
    PyAudioWPatch (Windows only).

    Loopback delivers no frames while nothing plays; `TrackWriter` pads that silence. `describe()` opens
    PyAudio once, resolves the device and keeps both until `stop()`, so `start()` opens the very device
    and format the WAV header was written for, without enumerating the devices a second time.
    """

    def __init__(self, output: str = "") -> None:
        self._output = output
        self._pa = None
        self._stream = None
        self._dev: dict | None = None

    def _find_device(self, pa, pyaudio) -> dict:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if self._output:
            for i in range(wasapi["deviceCount"]):
                dev = pa.get_device_info_by_host_api_device_index(wasapi["index"], i)
                if dev["name"] == self._output and dev["maxOutputChannels"] > 0 and not dev.get("isLoopbackDevice"):
                    speakers = dev
                    break
            else:
                logger.warning("Meeting output %r not found; using the default output", self._output)
        if speakers.get("isLoopbackDevice"):
            return speakers
        loopbacks = list(pa.get_loopback_device_info_generator())
        # Loopback devices are named "<output> [Loopback]": the exact name first, so an output whose name
        # contains another's ("Speakers" / "Speakers 2") is never mistaken for it.
        for loopback in loopbacks:
            if loopback["name"] == f"{speakers['name']} [Loopback]":
                return loopback
        for loopback in loopbacks:
            if speakers["name"] in loopback["name"]:
                return loopback
        raise RuntimeError(f"No loopback device for output {speakers['name']!r}")

    def describe(self) -> AudioFormat:
        import pyaudiowpatch as pyaudio

        self.stop()
        self._pa = pyaudio.PyAudio()
        try:
            self._dev = self._find_device(self._pa, pyaudio)
        except Exception:
            self.stop()
            raise
        return AudioFormat(int(self._dev["defaultSampleRate"]), int(self._dev["maxInputChannels"]))

    def start(self, sink: Callable[[bytes], None]) -> None:
        import pyaudiowpatch as pyaudio

        if self._dev is None:
            self.describe()
        dev = self._dev
        assert self._pa is not None and dev is not None

        def callback(in_data, frame_count, time_info, status):
            sink(in_data)
            return None, pyaudio.paContinue

        try:
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
        self._dev = None


class TrackWriter:
    """Streams one track to a WAV file and keeps it aligned with the shared start time.

    `write` is told when each block arrived. A block's audio ends at its arrival, so the frames the track
    should hold before it follow from the clock. The very first block pads the whole offset from `t0`. After
    that a difference within `GAP_TOLERANCE_S` is device jitter and left alone; a larger deficit (a loopback
    that sent nothing during silence) is filled with zeros, and a larger surplus (a device clock running
    fast) is dropped from the start of the block. So a track never drifts more than the tolerance from the
    clock, and `finish()` pads it up to the stop time.
    """

    def __init__(self, path: Path, fmt: AudioFormat, t0: float) -> None:
        self.path = path
        self.fmt = fmt
        self._t0 = t0
        self.frames = 0
        self._started = False
        self._closed = False
        self.frame_bytes = SAMPLE_WIDTH * fmt.channels
        self._wav = wave.open(str(path), "wb")  # noqa: SIM115 - closed in finish()
        self._wav.setnchannels(fmt.channels)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(fmt.sample_rate)

    def pad(self, frames: int) -> None:
        slice_frames = max(1, int(PAD_SLICE_S * self.fmt.sample_rate))
        while frames > 0:
            n = min(frames, slice_frames)
            self._wav.writeframes(bytes(n * self.frame_bytes))
            self.frames += n
            frames -= n

    def write(self, data: bytes, arrival: float) -> None:
        block_frames = len(data) // self.frame_bytes
        data = data[: block_frames * self.frame_bytes]
        deficit = round((arrival - self._t0) * self.fmt.sample_rate) - block_frames - self.frames
        tolerance = GAP_TOLERANCE_S * self.fmt.sample_rate if self._started else 0
        self._started = True
        if deficit > tolerance:
            self.pad(deficit)
        elif -deficit > tolerance:
            drop = min(block_frames, -deficit)
            data = data[drop * self.frame_bytes :]
            block_frames -= drop
        self._wav.writeframes(data)
        self.frames += block_frames

    def finish(self, end: float) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.pad(round((end - self._t0) * self.fmt.sample_rate) - self.frames)
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
        self._lock = threading.Lock()
        self._lost_frames = 0
        self.dropped = 0
        self.error: str | None = None

    def sink(self, data: bytes) -> None:
        """Runs on the audio thread: never blocks, a full queue drops the block."""
        try:
            self._queue.put_nowait((self._clock(), data))
        except queue.Full:
            with self._lock:
                self.dropped += 1
                self._lost_frames += len(data) // self.writer.frame_bytes

    def _take_lost(self) -> int:
        with self._lock:
            lost, self._lost_frames = self._lost_frames, 0
        return lost

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            if self.error is None:
                try:
                    # Blocks lost to a full queue were part of the timeline: put their silence back first.
                    self.writer.pad(self._take_lost())
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

    def end(self, end: float) -> None:
        """Drain the queue, then close the file; a failure is recorded, never raised."""
        if self._thread.is_alive():
            self._queue.put(None)
            self._thread.join()
        try:
            if self.error is None:
                self.writer.pad(self._take_lost())
            self.writer.finish(end)
        except Exception as exc:
            self.error = self.error or str(exc)
            logger.error("Closing meeting %s track failed: %s", self.name, exc)


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
        self._t0 = 0.0
        self._dictations: list[dict[str, float]] = []
        self._dictation_lock = threading.Lock()
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

    def _stop_sources(self) -> None:
        for name, source in self._sources.items():
            try:
                source.stop()
            except Exception:
                logger.exception("Stopping meeting source %s failed", name)

    def start(self) -> Path:
        """Begin recording; on failure nothing is left behind (no folder, no open file) and the error is raised."""
        if self.recording:
            raise RuntimeError("Meeting recording already running")
        folder: Path | None = None
        tracks: dict[str, _Track] = {}
        try:
            # Resolve every device before the clock starts: t0 must not include device enumeration.
            formats = {name: source.describe() for name, source in self._sources.items()}
            self._started_at = self._now()
            folder = self._new_folder(self._started_at)
            t0 = self._clock()
            self._t0 = t0
            self._dictations = []
            for name, source in self._sources.items():
                writer = TrackWriter(folder / f"{name}.wav", formats[name], t0)
                tracks[name] = _Track(name, source, writer, self._clock)
            self.folder = folder
            self._tracks = tracks
            self._write_meta(ended_at=None)
            for track in tracks.values():
                track.begin()
            for track in tracks.values():
                track.source.start(track.sink)
        except BaseException:
            self._abort(tracks, folder)
            raise
        logger.info("Meeting recording started: %s", folder)
        return folder

    def _abort(self, tracks: dict[str, _Track], folder: Path | None) -> None:
        """Undo a failed start: stop every source, close every file, delete the folder."""
        self._tracks = {}
        self._stop_sources()
        for track in tracks.values():
            track.end(self._clock())
        self.folder = None
        self._started_at = None
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)

    def add_dictation(self, start: float, end: float) -> None:
        """Store a dictation take (recorder clock values) as seconds from the meeting start in `meeting.json`.

        Ignored when no meeting runs; a take that began before the meeting is cut at its start. Stored whatever
        `[meeting].leave_dictation_out` says: the setting is applied when the meeting is transcribed.
        """
        if not self.recording:
            return
        with self._dictation_lock:
            self._dictations.append(
                {"start_s": round(max(0.0, start - self._t0), 3), "end_s": round(end - self._t0, 3)}
            )
            try:
                self._write_meta(ended_at=None)
            except OSError:
                logger.exception("Storing the dictation range failed")

    def flush(self) -> None:
        """Wait until every block received so far is on disk."""
        for track in self._tracks.values():
            track.flush()

    def stop(self) -> Path:
        """Stop recording. Every track is closed even if another fails; failures land in `meeting.json`."""
        if not self.recording:
            raise RuntimeError("No meeting recording running")
        tracks = self._tracks
        self._stop_sources()
        end = self._clock()
        try:
            for track in tracks.values():
                track.end(end)
        finally:
            with self._dictation_lock:
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
            "dictations": list(self._dictations),
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
    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    try:
        # A timed wait: an untimed lock wait is not interrupted by Ctrl+C on Windows.
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    print(f"Saved {recorder.stop()}")


if __name__ == "__main__":
    main()
