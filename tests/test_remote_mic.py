"""Tests for the remote mic: the phone's WebSocket contract, the audio source that stands in
for the microphone, the [remote_mic] config section, and how the app hands a phone
recording to the transcription cycle."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from nexusvox.config import Config, RemoteMicConfig, load_config, save_config
from nexusvox.remote_mic import PATH, RemoteAudioSource, RemoteMicServer

TOKEN = "s3cret"


class _Phone:
    """Records what the server asked of the app."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.starts = 0
        self.stops = 0
        self.cancels = 0

    def on_start(self) -> bool:
        self.starts += 1
        return self.accept

    def on_stop(self) -> None:
        self.stops += 1

    def on_cancel(self) -> None:
        self.cancels += 1


async def _serve(phone: _Phone, token: str = TOKEN) -> tuple[RemoteMicServer, RemoteAudioSource, str]:
    source = RemoteAudioSource()
    config = RemoteMicConfig(enabled=True, host="127.0.0.1", port=0, token=token)
    server = RemoteMicServer(config, source, phone.on_start, phone.on_stop, phone.on_cancel)
    asyncio.create_task(server.run())
    for _ in range(100):
        if server._server is not None:
            break
        await asyncio.sleep(0.01)
    port = server._server.sockets[0].getsockname()[1]
    return server, source, f"ws://127.0.0.1:{port}{PATH}"


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _recv(ws) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), 2))


async def _drain(source: RemoteAudioSource) -> list[bytes]:
    return [chunk async for chunk in source.chunks()]


# --- wire contract -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recording_flows_audio_into_the_source_and_ends_with_done():
    phone = _Phone()
    server, source, url = await _serve(phone)
    async with connect(url, additional_headers=_auth()) as ws:
        assert await _recv(ws) == {"type": "ready", "sample_rate": 16000}
        await ws.send(json.dumps({"type": "start"}))
        await ws.send(b"\x01\x00\x02\x00")
        await ws.send(b"\x03\x00")
        await ws.send(json.dumps({"type": "stop"}))
        for _ in range(100):
            if phone.stops:
                break
            await asyncio.sleep(0.01)
        assert (phone.starts, phone.stops) == (1, 1)

        source.stop()
        assert await _drain(source) == [b"\x01\x00\x02\x00", b"\x03\x00"]

        server.finished("Hallo Welt")
        assert await _recv(ws) == {"type": "done", "text": "Hallo Welt"}
    await server.close()


@pytest.mark.asyncio
async def test_audio_outside_a_recording_is_dropped():
    phone = _Phone()
    server, source, url = await _serve(phone)
    async with connect(url, additional_headers=_auth()) as ws:
        await _recv(ws)
        await ws.send(b"\x09\x00")
        await ws.send(json.dumps({"type": "start"}))
        await ws.send(b"\x01\x00")
        await ws.send(json.dumps({"type": "stop"}))
        await ws.send(b"\x07\x00")
        for _ in range(100):
            if phone.stops:
                break
            await asyncio.sleep(0.01)
    source.stop()
    assert await _drain(source) == [b"\x01\x00"]
    await server.close()


@pytest.mark.asyncio
async def test_cancel_ends_the_recording_without_a_stop():
    phone = _Phone()
    server, _, url = await _serve(phone)
    async with connect(url, additional_headers=_auth()) as ws:
        await _recv(ws)
        await ws.send(json.dumps({"type": "start"}))
        await ws.send(json.dumps({"type": "cancel"}))
        for _ in range(100):
            if phone.cancels:
                break
            await asyncio.sleep(0.01)
    assert (phone.stops, phone.cancels) == (0, 1)
    await server.close()


@pytest.mark.asyncio
async def test_busy_app_answers_busy():
    phone = _Phone(accept=False)
    server, _, url = await _serve(phone)
    async with connect(url, additional_headers=_auth()) as ws:
        await _recv(ws)
        await ws.send(json.dumps({"type": "start"}))
        assert await _recv(ws) == {"type": "busy"}
    await server.close()


@pytest.mark.asyncio
async def test_second_phone_is_busy_until_the_first_recording_is_finished():
    phone = _Phone()
    server, _, url = await _serve(phone)
    async with connect(url, additional_headers=_auth()) as first, connect(url, additional_headers=_auth()) as second:
        await _recv(first)
        await _recv(second)
        await first.send(json.dumps({"type": "start"}))
        await first.send(json.dumps({"type": "stop"}))
        await second.send(json.dumps({"type": "start"}))
        assert await _recv(second) == {"type": "busy"}

        server.finished()
        assert await _recv(first) == {"type": "done", "text": ""}
        await second.send(json.dumps({"type": "start"}))
        for _ in range(100):
            if phone.starts == 2:
                break
            await asyncio.sleep(0.01)
        assert phone.starts == 2
    await server.close()


@pytest.mark.asyncio
async def test_phone_dropping_mid_recording_stops_it():
    phone = _Phone()
    server, _, url = await _serve(phone)
    async with connect(url, additional_headers=_auth()) as ws:
        await _recv(ws)
        await ws.send(json.dumps({"type": "start"}))
        await ws.send(b"\x01\x00")
    for _ in range(100):
        if phone.stops:
            break
        await asyncio.sleep(0.01)
    assert phone.stops == 1
    await server.close()


@pytest.mark.asyncio
async def test_wrong_token_is_refused():
    server, _, url = await _serve(_Phone())
    with pytest.raises(InvalidStatus) as exc:
        async with connect(url, additional_headers=_auth("wrong")):
            pass
    assert exc.value.response.status_code == 401
    with pytest.raises(InvalidStatus) as exc:
        async with connect(url):
            pass
    assert exc.value.response.status_code == 401
    await server.close()


@pytest.mark.asyncio
async def test_wrong_path_is_not_found():
    server, _, url = await _serve(_Phone())
    with pytest.raises(InvalidStatus) as exc:
        async with connect(url.replace(PATH, "/other"), additional_headers=_auth()):
            pass
    assert exc.value.response.status_code == 404
    await server.close()


@pytest.mark.asyncio
async def test_empty_token_never_listens():
    config = RemoteMicConfig(enabled=True, port=0, token="")
    server = RemoteMicServer(config, RemoteAudioSource(), lambda: True, lambda: None, lambda: None)
    await asyncio.wait_for(server.run(), 1)
    assert server._server is None


# --- audio source ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_drops_leftovers_of_a_skipped_recording():
    source = RemoteAudioSource()
    source.feed(b"old")
    source.reset()
    source.feed(b"new")
    source.stop()
    assert await _drain(source) == [b"new"]


# --- config ----------------------------------------------------------------------------


def test_remote_mic_is_off_by_default(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlanguage = "en"\n', encoding="utf-8")
    assert load_config(path).remote_mic == RemoteMicConfig()


def test_remote_mic_survives_a_save(tmp_path: Path):
    path = tmp_path / "config.toml"
    config = Config()
    config.remote_mic = RemoteMicConfig(enabled=True, host="100.100.61.50", port=47400, token=TOKEN)
    save_config(config, path)
    assert load_config(path).remote_mic == config.remote_mic


# --- app -------------------------------------------------------------------------------

try:
    from nexusvox.app import NexusVoxApp
except Exception:  # Windows-only imports (pynput hook, tray, SendInput) on other platforms
    NexusVoxApp = None

needs_app = pytest.mark.skipif(NexusVoxApp is None, reason="nexusvox.app needs the Windows runtime")


def _app(*, hotkey_active: bool = False) -> "NexusVoxApp":
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config()
    app._loop = MagicMock()
    app._hotkey = MagicMock(active=hotkey_active, command=True)
    app._mic_guard = MagicMock()
    app._remote_audio = RemoteAudioSource()
    app._remote_recording = False
    app._remote_cancelled = False
    app._cycle_running = False
    app._record_start_event = asyncio.Event()
    app._record_stop_event = asyncio.Event()
    return app


@needs_app
def test_remote_start_queues_a_cycle():
    app = _app()
    app._record_stop_event.set()  # a stale release must not end the phone's recording
    assert app._on_remote_start() is True
    assert app._remote_recording is True
    assert app._record_start_event.is_set()
    assert not app._record_stop_event.is_set()


@needs_app
@pytest.mark.parametrize("busy", ["cycle", "hotkey", "queued"])
def test_remote_start_is_refused_while_busy(busy):
    app = _app(hotkey_active=busy == "hotkey")
    app._cycle_running = busy == "cycle"
    if busy == "queued":
        app._record_start_event.set()
    assert app._on_remote_start() is False
    assert app._remote_recording is False


@needs_app
def test_hotkey_is_ignored_during_a_remote_recording():
    app = _app()
    app._on_remote_start()
    app._on_hotkey_activate()
    app._on_hotkey_deactivate()
    app._loop.call_soon_threadsafe.assert_not_called()
    app._mic_guard.hold_async.assert_not_called()


@needs_app
def test_remote_cycle_records_from_the_phone_and_is_never_a_command():
    app = _app()
    app._audio = MagicMock()
    app._transcriber = MagicMock()
    app._transcriber.connect = AsyncMock()
    app._transcriber.disconnect = AsyncMock()
    app._transcriber.send_audio_chunk = AsyncMock()
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="  "))
    app._tray = MagicMock()
    app._send_os_command = MagicMock()

    async def cycle():
        app._record_stop_event = asyncio.Event()
        app._remote_audio.feed(b"\x01\x00")
        task = asyncio.create_task(app._transcription_cycle(remote=True))
        await asyncio.sleep(0.05)
        app._record_stop_event.set()
        await task

    asyncio.run(cycle())
    app._audio.start.assert_not_called()
    app._transcriber.send_audio_chunk.assert_awaited_once_with(b"\x01\x00")
    app._send_os_command.assert_not_called()
    app._hotkey.cancel.assert_not_called()


@needs_app
def test_cancelled_remote_cycle_transcribes_nothing():
    app = _app()
    app._transcriber = MagicMock()
    app._transcriber.connect = AsyncMock()
    app._transcriber.disconnect = AsyncMock()
    app._transcriber.send_audio_chunk = AsyncMock()
    app._transcriber.finish = AsyncMock()
    app._tray = MagicMock()

    async def cycle():
        app._record_stop_event = asyncio.Event()
        task = asyncio.create_task(app._transcription_cycle(remote=True))
        await asyncio.sleep(0.05)
        app._on_remote_cancel()
        await task

    asyncio.run(cycle())
    app._transcriber.finish.assert_not_awaited()
    app._transcriber.disconnect.assert_awaited_once()


@needs_app
def test_remote_text_goes_back_to_the_phone_and_is_not_typed(monkeypatch):
    app = _app()
    app._config.database.store_audio = False
    app._config.assistant.enabled = True
    app._remote_text = ""
    app._db = MagicMock()
    app._db.get_dictionary_entries.return_value = []
    app._transcriber = MagicMock()
    app._transcriber.connect = AsyncMock()
    app._transcriber.disconnect = AsyncMock()
    app._transcriber.send_audio_chunk = AsyncMock()
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="nexus assistant hallo welt", confidence=None))
    app._tray = MagicMock()
    inject = MagicMock()
    monkeypatch.setattr("nexusvox.app.inject_text", inject)
    assistant = MagicMock()
    monkeypatch.setattr("nexusvox.app.send_to_assistant", assistant)

    async def cycle():
        app._record_stop_event = asyncio.Event()
        task = asyncio.create_task(app._transcription_cycle(remote=True))
        await asyncio.sleep(0.05)
        app._record_stop_event.set()
        await task

    asyncio.run(cycle())
    inject.assert_not_called()
    assistant.assert_not_called()
    assert app._remote_text == "nexus assistant hallo welt"
