"""Remote microphone: a phone uses NexusVox as its speech recogniser over a WebSocket.

The phone keeps one connection open while its remote mode is on, so an open connection
doubles as "NexusVox is running and reachable". A recording is a `start` message, binary
audio frames (16 kHz mono PCM16 little-endian), then a `stop` message; NexusVox runs its
transcription cycle on that audio (dictionary, voice commands, translation) and sends the
text back in `done`, for the phone to insert into its own focused field. Nothing is typed
on the PC. The wire contract is in docs/REMOTE_MIC.md.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator, Callable
from http import HTTPStatus

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .config import RemoteMicConfig

logger = logging.getLogger(__name__)

PATH = "/v1/remote-mic"
SAMPLE_RATE = 16_000
_BIND_RETRY_S = 30.0


class RemoteAudioSource:
    """Stands in for `AudioCapture` during a remote recording: same `start`/`stop`/`chunks`.

    Frames are queued from the moment the phone says `start`, before the transcription
    cycle has connected to its server, so the first syllable is not lost.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def reset(self) -> None:
        """Drop whatever a skipped or aborted recording left behind."""
        self._queue = asyncio.Queue()

    def feed(self, chunk: bytes) -> None:
        self._queue.put_nowait(chunk)

    def start(self) -> None:
        """Nothing to open: the phone is already sending."""

    def stop(self) -> None:
        self._queue.put_nowait(None)

    async def chunks(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk


class RemoteMicServer:
    """WebSocket server for remote recordings. Several phones may stay connected; one of
    them records at a time.

    `on_start` returns False when NexusVox is busy (a cycle is running or the hotkey is
    held), and the phone gets `busy`. `on_stop` ends the recording; a phone that drops
    mid-recording ends it too, so a killed app never leaves NexusVox waiting. `on_cancel`
    ends it without transcribing (the phone's button turned out to be dragged).
    """

    def __init__(
        self,
        config: RemoteMicConfig,
        source: RemoteAudioSource,
        on_start: Callable[[], bool],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self._config = config
        self._source = source
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cancel = on_cancel
        self._server: Server | None = None
        self._owner: ServerConnection | None = None
        self._streaming = False
        self._closed = False
        self._pending: set[asyncio.Future[None]] = set()

    async def run(self) -> None:
        """Bind and serve until `close()`. A bind failure (say, Tailscale not up yet) is
        retried, so NexusVox can start before the network does."""
        if not self._config.token:
            logger.error("Remote mic is enabled but [remote_mic].token is empty; not starting it")
            return
        while not self._closed:
            try:
                self._server = await serve(
                    self._handle,
                    self._config.host,
                    self._config.port,
                    process_request=self._check_request,
                    max_size=1 << 20,
                )
            except OSError as exc:
                logger.warning(
                    "Remote mic could not bind %s:%d (%s); retrying in %.0fs",
                    self._config.host,
                    self._config.port,
                    exc,
                    _BIND_RETRY_S,
                )
                await asyncio.sleep(_BIND_RETRY_S)
                continue
            logger.info("Remote mic listening on ws://%s:%d%s", self._config.host, self._config.port, PATH)
            await self._server.wait_closed()
            return

    async def close(self) -> None:
        self._closed = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def finished(self, text: str = "") -> None:
        """Called by the app when the cycle of a remote recording is over, with the text for
        the phone to insert ("" when nothing was recognised, cancelled or failed)."""
        owner, self._owner = self._owner, None
        self._streaming = False
        if owner is not None:
            # Keep a reference: the loop holds tasks weakly, and an unreferenced send
            # can be garbage-collected before `done` reaches the phone.
            task = asyncio.ensure_future(self._send(owner, {"type": "done", "text": text}))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    def _check_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path.split("?", 1)[0] != PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        expected = f"Bearer {self._config.token}"
        given = request.headers.get("Authorization", "")
        if not hmac.compare_digest(given.encode(), expected.encode()):
            logger.warning("Remote mic refused a connection from %s: bad token", connection.remote_address)
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    async def _handle(self, connection: ServerConnection) -> None:
        logger.info("Remote mic connected: %s", connection.remote_address)
        await self._send(connection, {"type": "ready", "sample_rate": SAMPLE_RATE})
        try:
            async for message in connection:
                if isinstance(message, bytes):
                    if connection is self._owner and self._streaming:
                        self._source.feed(message)
                    continue
                try:
                    kind = json.loads(message).get("type")
                except (ValueError, AttributeError):
                    kind = None
                if kind == "start":
                    await self._start(connection)
                elif kind == "stop":
                    self._stop(connection)
                elif kind == "cancel":
                    self._stop(connection, cancel=True)
                else:
                    logger.warning("Remote mic ignored message: %.80s", message)
        except ConnectionClosed:
            pass
        finally:
            logger.info("Remote mic disconnected: %s", connection.remote_address)
            self._stop(connection)

    async def _start(self, connection: ServerConnection) -> None:
        if self._owner is not None:
            await self._send(connection, {"type": "busy"})
            return
        self._source.reset()
        if not self._on_start():
            await self._send(connection, {"type": "busy"})
            return
        self._owner = connection
        self._streaming = True

    def _stop(self, connection: ServerConnection, cancel: bool = False) -> None:
        if connection is not self._owner or not self._streaming:
            return
        # The owner stays until `finished()`, so a second phone cannot start meanwhile.
        self._streaming = False
        if cancel:
            self._on_cancel()
        else:
            self._on_stop()

    @staticmethod
    async def _send(connection: ServerConnection, payload: dict) -> None:
        try:
            await connection.send(json.dumps(payload))
        except ConnectionClosed:
            pass
