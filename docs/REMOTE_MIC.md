# The remote mic — NexusVox as the speech recogniser for a phone

`[remote_mic]` lets a phone use NexusVox's model and pipeline. Hold a button on the phone,
speak, let go: the audio streams to NexusVox, which runs the same cycle as the hotkey
(transcription, correction dictionary, voice commands, translation) and sends the text
back, and the phone inserts it into its own focused text field. Nothing is typed on the PC,
and PC actions ("nexus assistant ...", nexus window commands, command recordings) are not
run for phone recordings. The phone app is **not part of NexusVox**; any app that speaks
the contract below works.

Off by default. With it off, or with an empty token, nothing listens.

## Configuration

`config.toml`:

```toml
[remote_mic]
enabled = true
host = "100.x.y.z"      # this PC's Tailscale IP, or another private address
port = 47393
token = "a long random string"
```

Whoever connects can use your PC's speech model and read back what it hears. Bind `host`
to an address only your own devices reach (a Tailscale or other VPN address), never
`0.0.0.0` on an untrusted network, and use a long random token, for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
If the address does not exist yet when NexusVox starts (the VPN comes up later), the bind
is retried every 30 seconds.

## The contract

A WebSocket at `ws://host:port/v1/remote-mic`. The handshake must carry
`Authorization: Bearer <token>`; a wrong or missing token gets HTTP 401, another path 404.

The phone keeps the connection open while its remote mode is on. An open connection means
NexusVox is running and reachable; a closed one means it is not, and the phone should
reconnect with a back-off.

Server to phone, JSON text frames:

| Message | When |
| --- | --- |
| `{"type": "ready", "sample_rate": 16000}` | Right after the handshake |
| `{"type": "busy"}` | A `start` was refused: a recording is running, queued, or the hotkey is held |
| `{"type": "done", "text": "..."}` | The recording's cycle is over. `text` is what to insert, `""` when nothing was recognised or the cycle was cancelled, skipped or failed |

Phone to server:

| Message | Meaning |
| --- | --- |
| `{"type": "start"}` | Begin a recording |
| binary frames | Audio: 16 kHz, mono, PCM16 little-endian, any frame size (100 ms is a good choice) |
| `{"type": "stop"}` | End the recording and get its text back in `done` |
| `{"type": "cancel"}` | End the recording and drop it, nothing is transcribed |

Audio is only accepted between an accepted `start` and its `stop` or `cancel`; frames
outside that window are dropped. Frames sent right after `start` are buffered while
NexusVox connects to its transcription server, so the phone can stream immediately.
`done` follows a `cancel` too. Dropping the connection during a recording ends it as if
`stop` had been sent.

One recording at a time, across all connected phones and the hotkey: while a phone records,
the hotkey is ignored, and a second phone gets `busy` until the first one's `done`. A
remote recording is never a command recording (`[os_control]`).
