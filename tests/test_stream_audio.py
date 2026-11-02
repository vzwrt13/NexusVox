"""A streaming error must not leave the audio queue out of step with the recordings that follow."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nexusvox.app import NexusVoxApp
from nexusvox.config import AudioConfig


class QueueAudio:
    """The part of AudioCapture the streaming reads: one queue that outlives the recordings."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def record(self, chunks: list[bytes]) -> None:
        for chunk in chunks:
            self.queue.put_nowait(chunk)
        self.queue.put_nowait(None)  # what stop() adds

    async def chunks(self):
        while True:
            chunk = await self.queue.get()
            if chunk is None:
                break
            yield chunk


class RecordingTranscriber:
    """Collects what each cycle sent; raises on the chunk numbers in `fail_on` (1-based, over all cycles)."""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.sent: list[bytes] = []
        self.seen = 0

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        self.seen += 1
        if self.seen in self.fail_on:
            raise RuntimeError("Not connected")
        self.sent.append(pcm16_bytes)


def _app(transcriber) -> NexusVoxApp:
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._transcriber = transcriber
    return app


def _chunks(tag: int, n: int) -> list[bytes]:
    return [bytes([tag]) * 4] * n


async def test_after_a_streaming_error_the_next_two_cycles_stream_exactly_their_own_audio():
    transcriber = RecordingTranscriber(fail_on={2})
    app = _app(transcriber)
    audio = QueueAudio()

    audio.record(_chunks(1, 6))  # cycle 1: the second chunk fails
    with pytest.raises(RuntimeError):
        await app._stream_audio(audio)
    assert audio.queue.empty()  # every chunk and the end marker were read: nothing leaks into the next cycle

    transcriber.sent.clear()
    audio.record(_chunks(2, 3))  # cycle 2
    buffer = await app._stream_audio(audio)
    assert transcriber.sent == _chunks(2, 3)
    assert bytes(buffer) == b"".join(_chunks(2, 3))

    transcriber.sent.clear()
    audio.record(_chunks(3, 5))  # cycle 3
    buffer = await app._stream_audio(audio)
    assert transcriber.sent == _chunks(3, 5)
    assert len(buffer) == 5 * 4


async def test_the_whole_recording_is_kept_when_sending_fails_midway():
    app = _app(RecordingTranscriber(fail_on={3}))
    audio = QueueAudio()
    audio.record(_chunks(1, 5))
    with pytest.raises(RuntimeError):
        await app._stream_audio(audio)
    assert audio.queue.empty()


async def test_audio_capture_starts_every_recording_with_an_empty_queue(monkeypatch):
    import nexusvox.audio as audio_module

    stream = MagicMock()
    monkeypatch.setattr(audio_module.sd, "InputStream", lambda **kw: stream)
    monkeypatch.setattr(audio_module.sd, "query_devices", lambda kind=None: {"name": "mic"})
    capture = audio_module.AudioCapture(AudioConfig())
    capture._queue.put_nowait(b"left behind by an aborted recording")
    capture._queue.put_nowait(None)

    capture.start()

    assert capture._queue.empty()
    capture.stop()


async def test_a_failed_cycle_ends_its_stream_task_and_closes_the_microphone():
    app = _app(RecordingTranscriber())
    audio = MagicMock()
    task = asyncio.create_task(asyncio.sleep(60))  # a stream still reading when the cycle died
    await app._abort_recording(audio, task, recording_stopped=False)
    audio.stop.assert_called_once()
    assert task.cancelled()

    audio.reset_mock()
    await app._abort_recording(audio, None, recording_stopped=True)
    audio.stop.assert_not_called()  # already stopped: a second end marker would leak into the next recording
