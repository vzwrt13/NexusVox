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
    monkeypatch.setattr(audio_module.sd, "InputStream", lambda **kw: MagicMock())
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
