"""One summary line per recording, and a warning when the whole recording was silent."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import numpy as np
import pytest

from nexusvox.config import AudioConfig

try:
    import nexusvox.audio.capture as audio_module
except Exception:  # PortAudio is missing on this platform
    pytest.skip("nexusvox.audio.capture needs sounddevice", allow_module_level=True)

FRAMES = AudioConfig().chunk_size // 2


def _recording(monkeypatch, device: str = "Desk mic") -> audio_module.AudioCapture:
    monkeypatch.setattr(audio_module.sd, "InputStream", lambda **kw: MagicMock(), raising=True)
    monkeypatch.setattr(audio_module.sd, "query_devices", lambda kind=None: {"name": device})
    capture = audio_module.AudioCapture(AudioConfig())
    capture.start()
    return capture


def _feed(capture: audio_module.AudioCapture, level: float, chunks: int) -> None:
    block = np.full((FRAMES, 1), level, dtype=np.float32)
    for _ in range(chunks):
        capture._audio_callback(block, FRAMES, {}, 0)


def _messages(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "nexusvox.audio" and r.levelno == level]


async def test_the_per_second_level_line_is_debug_only(monkeypatch, caplog):
    capture = _recording(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger="nexusvox.audio"):
        _feed(capture, 0.01, 40)
    assert any(m.startswith("Audio: chunks=") for m in _messages(caplog, logging.DEBUG))
    assert not [m for m in _messages(caplog, logging.INFO) if m.startswith("Audio: chunks=")]
    capture.stop()


async def test_stopping_logs_one_summary_line_with_length_levels_and_silent_share(monkeypatch, caplog):
    capture = _recording(monkeypatch)
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        _feed(capture, 0.0001, 2)  # -80 dB: silent
        _feed(capture, 0.01, 6)  # -40 dB
        _feed(capture, 0.1, 2)  # -20 dB peak
        capture.stop()
    summary = [m for m in _messages(caplog, logging.INFO) if m.startswith("Audio: ") and "chunks," in m]
    assert len(summary) == 1
    seconds = 10 * FRAMES / AudioConfig().sample_rate
    assert summary[0].startswith(f"Audio: {seconds:.1f} s, 10 chunks, rms avg ")
    assert "peak -20 dB" in summary[0]
    assert summary[0].endswith("20% silent")
    assert not _messages(caplog, logging.WARNING)  # speech was there


async def test_a_recording_silent_throughout_warns_with_the_device_name(monkeypatch, caplog):
    capture = _recording(monkeypatch, device="USB Headset")
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        _feed(capture, 0.0, 10)
        capture.stop()
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "USB Headset" in warnings[0]
    assert "100% silent" in _messages(caplog, logging.INFO)[-1]


async def test_a_short_silent_tap_does_not_warn(monkeypatch, caplog):
    capture = _recording(monkeypatch)
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        _feed(capture, 0.0, 2)
        capture.stop()
    assert not _messages(caplog, logging.WARNING)


async def test_each_recording_counts_from_zero_and_a_second_stop_logs_nothing(monkeypatch, caplog):
    capture = _recording(monkeypatch)
    _feed(capture, 0.0, 10)
    capture.stop()
    capture.start()
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        _feed(capture, 0.05, 3)
        capture.stop()
        capture.stop()
    summary = [m for m in _messages(caplog, logging.INFO) if "chunks," in m]
    assert len(summary) == 1
    assert ", 3 chunks," in summary[0]
    assert summary[0].endswith("0% silent")


async def test_one_click_at_the_start_of_a_muted_recording_still_warns(monkeypatch, caplog):
    capture = _recording(monkeypatch, device="Blocked mic")
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        _feed(capture, 0.3, 1)  # the click when the stream opens
        _feed(capture, 0.0, 39)
        capture.stop()
    assert "98% silent" in _messages(caplog, logging.INFO)[-1]
    assert "Blocked mic" in _messages(caplog, logging.WARNING)[0]


async def test_the_length_adds_up_the_frames_the_callbacks_delivered(monkeypatch, caplog):
    capture = _recording(monkeypatch)
    rate = AudioConfig().sample_rate
    block = np.full((rate // 10, 1), 0.01, dtype=np.float32)  # 100 ms blocks, not the configured chunk size
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        for _ in range(20):
            capture._audio_callback(block, len(block), {}, 0)
        capture.stop()
    assert _messages(caplog, logging.INFO)[-1].startswith("Audio: 2.0 s, 20 chunks")


async def test_a_device_that_fails_to_stop_still_leaves_the_summary(monkeypatch, caplog):
    capture = _recording(monkeypatch)
    stream = capture._stream
    stream.stop.side_effect = OSError("device unplugged")
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        _feed(capture, 0.0, 10)
        capture.stop()  # logged, not raised: the take is kept
    stream.close.assert_called_once()  # closed although stopping failed: nothing left feeding the next take
    assert any("10 chunks" in m for m in _messages(caplog, logging.INFO))
    assert any("was silent" in m for m in _messages(caplog, logging.WARNING))
    assert capture._stream is None


async def test_a_new_recording_closes_a_stream_an_earlier_stop_left_open(monkeypatch):
    first = _recording(monkeypatch)
    old_stream = first._stream
    _feed(first, 0.1, 5)

    first.start()

    old_stream.stop.assert_called_once()
    old_stream.close.assert_called_once()
    assert first._levels.chunks == 0
    first.stop()


async def test_a_microphone_that_fails_to_close_still_ends_the_take_with_its_audio(monkeypatch, caplog):
    from nexusvox.audio import state as audio_state

    capture = _recording(monkeypatch)
    _feed(capture, 0.05, 3)
    capture._stream.stop.side_effect = OSError("device unplugged")  # the mic was pulled out at key-up
    open_before = audio_state.streams_open()
    with caplog.at_level(logging.INFO, logger="nexusvox.audio"):
        capture.stop()  # never raises: the take is kept
    chunks = [chunk async for chunk in capture.chunks()]  # ends: the end marker was queued
    assert len(chunks) == 3
    assert audio_state.streams_open() == open_before - 1
    assert any("Closing the microphone failed" in m for m in _messages(caplog, logging.WARNING))
