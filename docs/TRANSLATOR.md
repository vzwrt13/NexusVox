# Translate to English — the external translator server

NexusVox does not translate. When *Translate to English* is on, it sends the transcript
to a local HTTP server, types whatever comes back, and stores the transcript as spoken.
That server is **not part of NexusVox**: you run it yourself, and you can use any
program that speaks the contract below.

The toggle is off by default. With it on and no server listening, the original text is
typed and the dashboard's *Language & Translation* card shows
"No translator at &lt;url&gt;".

## Configuration

`config.toml`:

```toml
[translator]
enabled = false                                # or flip the tray / dashboard toggle
url = "http://127.0.0.1:8003/translate"        # where your server listens
timeout_s = 8.0                                # fall back to the original text after this
```

## The contract

One endpoint, JSON in, JSON out.

**Request** — `POST <url>`, `Content-Type: application/json`:

```json
{"text": "Also ich glaube, das passt so."}
```

**Response** — `200`, JSON:

```json
{"text": "So, I think that is fine.", "source": "de", "ms": 400}
```

| Field    | Required | Meaning                                                              |
|----------|----------|----------------------------------------------------------------------|
| `text`   | yes      | The English text to type. Return the input unchanged for English.    |
| `source` | no       | What the server saw: `"de"`, `"en"` or `"mixed"`. Shown in analytics.|
| `ms`     | no       | The server's own processing time in milliseconds. Shown in analytics.|

Anything else — no answer, non-200, invalid JSON, missing `text`, timeout — makes
NexusVox type the original text and record the error on the transcription row.

The dashboard probes the endpoint with `{"text": ""}` to show whether a server is
reachable; any HTTP answer, including an error status, counts as reachable.

## Reference implementation

The maintainer runs the `translator` tool from a private personal-tooling repo:
TranslateGemma served by [Ollama](https://ollama.com/), wrapped in a small FastAPI
server on port 8003 that translates German sentences and passes English ones through
unchanged, so mixed dictation works. It is not published. Any server that honours the
contract above — a few lines around an Ollama, LibreTranslate or DeepL call — works
the same way.
