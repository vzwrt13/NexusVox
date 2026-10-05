# Custom server — bring your own transcription backend

The model **Custom server** (`custom-server`) points NexusVox at a transcription server
you run yourself instead of one of the bundled models. NexusVox does not start, stop, or
health-check it; there is no Docker container behind it. Use it to:

- run a model on another machine in your network
- try a backend NexusVox does not bundle, through a small adapter you write
- compare a backend against your usual model: every transcript is saved with the model
  name you configure, so the dashboard Analytics tab shows both side by side

> **Privacy.** Every other model keeps your audio on this machine. With a custom server,
> each recording goes to whatever `url` you set. If that is another computer or a cloud
> service, your voice leaves this PC. NexusVox never does this unless you choose this
> model and set the URL yourself.

## Configuration

`config.toml`:

```toml
[inference]
model = "custom-server"

[inference.custom]
url = "http://localhost:8765/v1/audio/transcriptions"
protocol = "http"             # "http" or "realtime"
model_name = "custom-server"  # sent to the server, and stored with each transcript
```

Or pick **Custom server** under Settings → **Model** in the dashboard; the `url`,
`protocol`, and `model_name` still come from `config.toml`. The model overview shows the
URL in use. If `url` is empty the switch fails with an error and the previous model stays
active.

Switching to the custom server stops the previous model's container, if it had one. The
`[inference].server_url` setting is left untouched, so switching back to a bundled model
works as before.

Language detection, the correction dictionary, voice commands, and translation all run
on the returned text exactly as with the bundled models. Toggle mode (`[hotkey].mode`)
works with both protocols.

## Choosing a protocol

| | `http` | `realtime` |
|---|---|---|
| When audio is sent | All at once, after the recording stops | In chunks, while you speak |
| Wait after you stop | Full transcription time | Usually only the last few words |
| Server effort | One HTTP endpoint | A WebSocket session |

Both protocols type the text at the cursor once the recording has stopped. `realtime`
cuts the wait after you stop; it does not type words while you speak.

## The `http` contract

The same request NexusVox sends to the bundled HTTP models (OpenAI-compatible
`/v1/audio/transcriptions`):

- `POST` to `url`, `multipart/form-data`, with two fields:
  - `file` — `audio.wav`, 16 kHz, mono, 16-bit PCM WAV
  - `model` — the configured `model_name`
- Reply: status `200` with JSON `{"text": "<transcript>"}`. Other keys are ignored.
- A non-2xx status is logged and the recording produces no text. No reply within 30 s
  (120 s per chunk for dashboard file uploads) counts as an empty transcript.

## The `realtime` contract

The vLLM `/v1/realtime` WebSocket protocol that the bundled Voxtral model uses. NexusVox
opens one WebSocket per recording on `url` (`ws://` or `wss://`):

1. **Server → client:** `{"type": "session.created", "id": "<any>"}` — must be the first
   message; anything else aborts the recording.
2. **Client → server:** `{"type": "session.update", "model": "<model_name>"}`
3. **Client → server:** `{"type": "input_audio_buffer.commit"}` — no `final`; the session
   starts. Accept it before any audio arrives.
4. **Client → server, repeated while recording:**
   `{"type": "input_audio_buffer.append", "audio": "<base64>"}` — 16 kHz, mono, 16-bit
   little-endian PCM, no WAV header.
5. **Client → server, once, when the recording stops:**
   `{"type": "input_audio_buffer.commit", "final": true}`
6. **Server → client, any number, optional:** `{"type": "transcription.delta", "delta": "<text>"}`
7. **Server → client:** `{"type": "transcription.done", "text": "<full transcript>"}` —
   ends the recording; NexusVox closes the socket. If `text` is missing, the deltas
   joined together are used.

`{"type": "error", "error": "<message>"}` at any point fails the recording. Messages of any
other type are logged and ignored. No `transcription.done` within 30 s of the final commit
(120 s for dashboard file uploads) counts as an empty transcript.

## Writing an adapter

A server that already speaks one of the two contracts works as is. For anything else,
write a small adapter that speaks the contract on one side and the backend's own API on
the other. Keep its API keys in the adapter's environment, not in NexusVox's
`config.toml`. With `realtime`, forward each `input_audio_buffer.append` to the backend
as it arrives; that streaming is where the shorter wait comes from.
