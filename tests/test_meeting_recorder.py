"""Meeting recorder tests: fake sources and a fake clock, no audio devices."""

from __future__ import annotations

import json
import threading
import tracemalloc
import wave
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from nexusvox.meeting_recorder import GAP_TOLERANCE_S, AudioFormat, MeetingRecorder, TrackWriter


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSource:
    def __init__(self, fmt: AudioFormat) -> None:
        self.fmt = fmt
        self.sink = None
        self.stopped = False

    def describe(self) -> AudioFormat:
        return self.fmt

    def start(self, sink) -> None:
        self.sink = sink

    def stop(self) -> None:
        self.stopped = True

    def push(self, seconds: float) -> None:
        frames = round(seconds * self.fmt.sample_rate)
        self.sink(b"\x01\x00" * frames * self.fmt.channels)


def _recorder(tmp_path, mic_fmt, sys_fmt):
    clock = FakeClock()
    mic, system = FakeSource(mic_fmt), FakeSource(sys_fmt)
    rec = MeetingRecorder(tmp_path, mic, system, clock=clock, now=lambda: datetime(2026, 10, 26, 9, 0, tzinfo=UTC))
    return rec, mic, system, clock


def _wav_info(path):
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getnframes()


def test_writes_two_native_rate_tracks_and_meta(tmp_path):
    rec, mic, system, clock = _recorder(tmp_path, AudioFormat(16000, 1), AudioFormat(48000, 2))
    folder = rec.start()
    for _ in range(10):
        clock.advance(0.1)
        mic.push(0.1)
        system.push(0.1)
    rec.flush()
    assert rec.stop() == folder

    assert _wav_info(folder / "mic.wav") == (16000, 1, 16000)
    assert _wav_info(folder / "system.wav") == (48000, 2, 48000)
    meta = json.loads((folder / "meeting.json").read_text(encoding="utf-8"))
    assert meta["started_at"].startswith("2026-10-26T09:00")
    assert meta["ended_at"] is not None
    assert meta["tracks"]["system"]["sample_rate"] == 48000
    assert meta["tracks"]["system"]["frames"] == 48000
    assert not rec.recording


def test_silent_output_still_yields_full_length_track(tmp_path):
    rec, mic, system, clock = _recorder(tmp_path, AudioFormat(16000, 1), AudioFormat(48000, 2))
    folder = rec.start()
    for _ in range(100):
        clock.advance(0.1)
        mic.push(0.1)  # the loopback never delivers a frame
    rec.flush()
    rec.stop()
    assert _wav_info(folder / "system.wav") == (48000, 2, 10 * 48000)
    assert _wav_info(folder / "mic.wav")[2] == 10 * 16000


def test_gap_in_the_middle_is_padded_so_tracks_stay_aligned(tmp_path):
    rec, _mic, system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    folder = rec.start()
    clock.advance(1.0)
    system.push(1.0)
    clock.advance(30.0)  # nothing played for 30 s
    clock.advance(1.0)
    system.push(1.0)
    rec.flush()
    rec.stop()
    assert _wav_info(folder / "system.wav")[2] == 32 * 8000


def test_jitter_is_not_padded(tmp_path):
    rec, _mic, system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    folder = rec.start()
    for _ in range(20):
        clock.advance(0.1)
        system.push(0.1)
    rec.flush()
    rec.stop()
    assert _wav_info(folder / "system.wav")[2] == 2 * 8000


def test_one_hour_streams_to_disk_with_flat_memory(tmp_path):
    rate = 8000  # keeps the CI files small; the logic does not depend on the rate
    rec, mic, system, clock = _recorder(tmp_path, AudioFormat(rate, 1), AudioFormat(rate, 2))
    folder = rec.start()
    chunks = 36000  # 100 ms each = 60 minutes
    tracemalloc.start()
    peaks = []
    for i in range(1, chunks + 1):
        clock.advance(0.1)
        mic.push(0.1)
        if i % 3 != 0 or i < 9000:  # the output is silent between minute 15 and 60 in two of three blocks
            system.push(0.1)
        if i % 1000 == 0:
            rec.flush()
            peaks.append(tracemalloc.get_traced_memory()[0])
    rec.stop()
    tracemalloc.stop()

    assert _wav_info(folder / "mic.wav") == (rate, 1, 3600 * rate)
    assert _wav_info(folder / "system.wav") == (rate, 2, 3600 * rate)
    # Whole-take buffering would grow by ~57 MB per file; streaming keeps the traced memory level.
    assert max(peaks) - min(peaks) < 2_000_000


def test_second_start_while_recording_raises(tmp_path):
    rec, *_ = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    rec.start()
    with pytest.raises(RuntimeError):
        rec.start()
    rec.stop()


def test_each_meeting_gets_its_own_folder(tmp_path):
    rec, *_ = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    a = rec.start()
    rec.stop()
    b = rec.start()
    rec.stop()
    assert a != b


def test_failing_source_start_leaves_nothing_behind_and_raises_the_original_error(tmp_path):
    rec, mic, system, _clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))

    def boom(sink):
        raise RuntimeError("no device")

    system.start = boom
    # A cleanup failure must not mask the start error.
    original_finish = TrackWriter.finish

    def bad_finish(self, end):
        original_finish(self, end)
        raise OSError("disk")

    with patch.object(TrackWriter, "finish", bad_finish), pytest.raises(RuntimeError, match="no device"):
        rec.start()
    assert mic.stopped and system.stopped
    assert not rec.recording
    assert rec.folder is None
    assert list(tmp_path.iterdir()) == []


def test_stop_closes_every_track_and_writes_meta_when_one_fails(tmp_path):
    rec, mic, system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    folder = rec.start()
    clock.advance(1.0)
    mic.push(1.0)
    system.push(1.0)
    rec.flush()
    original_finish = TrackWriter.finish

    def bad_finish(self, end):
        original_finish(self, end)
        if self.path.name == "mic.wav":
            raise OSError("disk full")

    with patch.object(TrackWriter, "finish", bad_finish):
        rec.stop()
    assert not rec.recording
    meta = json.loads((folder / "meeting.json").read_text(encoding="utf-8"))
    assert meta["ended_at"] is not None
    assert "disk full" in meta["tracks"]["mic"]["error"]
    assert "error" not in meta["tracks"]["system"]
    assert _wav_info(folder / "system.wav")[2] == 8000


def test_first_block_pads_the_offset_from_start(tmp_path):
    rec, _mic, system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    folder = rec.start()
    clock.advance(0.2)  # below the jitter tolerance, but the lead-in is always aligned
    system.push(0.1)
    rec.flush()
    rec.stop()
    with wave.open(str(folder / "system.wav"), "rb") as w:
        lead_in = w.readframes(800)  # 0.2 s elapsed - 0.1 s block = 0.1 s of silence first
        first_audio = w.readframes(1)
    assert lead_in == bytes(1600)
    assert first_audio != bytes(2)


def test_fast_device_clock_is_trimmed(tmp_path):
    rec, _mic, system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    folder = rec.start()
    for _ in range(100):
        clock.advance(1.0)
        system.push(1.001)  # device clock 1000 ppm fast
    rec.flush()
    rec.stop()
    drift_s = abs(_wav_info(folder / "system.wav")[2] - 100 * 8000) / 8000
    assert drift_s <= GAP_TOLERANCE_S + 0.01


def test_blocks_dropped_on_a_full_queue_are_padded(tmp_path):
    rec, _mic, system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    folder = rec.start()
    track = rec._tracks["system"]
    gate = threading.Event()
    original_write = track.writer.write

    def slow_write(data, arrival):
        gate.wait()
        original_write(data, arrival)

    track.writer.write = slow_write
    # Fill the queue (the stalled writer thread holds one block) and overflow it.
    sent = 0
    while track.dropped == 0:
        clock.advance(0.05)
        system.push(0.05)
        sent += 1
    # The dropped block (0.05 s, under the tolerance) must be put back as silence.
    gate.set()
    rec.flush()
    rec.stop()
    assert track.dropped >= 1
    frames = _wav_info(folder / "system.wav")[2]
    assert frames == round(sent * 0.05 * 8000)


def test_format_from_describe_is_what_gets_written(tmp_path):
    rec, mic, _system, clock = _recorder(tmp_path, AudioFormat(8000, 1), AudioFormat(8000, 1))
    calls = []
    original = mic.describe
    mic.describe = lambda: calls.append(1) or original()
    rec.start()
    rec.stop()
    assert calls == [1]  # describe() once per start; start() does not resolve the device again
