# NexusVox — Core Runtime

## Transcriber Architecture

`BaseTranscriber` ABC (`needs_docker: bool = True` class attr) with a factory pattern. Three implementations:

- **`VoxtralRealtimeTranscriber`** — streams audio chunks over WebSocket to vLLM's `/v1/realtime` endpoint
- **`OpenAIHttpTranscriber`** — buffers audio in memory, POSTs a WAV file to an OpenAI-compatible `/v1/audio/transcriptions` HTTP endpoint (used by Cohere Transcribe, Parakeet TDT, and GPU-backed Whisper)
- **`LocalWhisperTranscriber`** (`transcribers/local_whisper.py`) — in-process faster-whisper, no Docker. Class-level `_model_cache` keeps loaded models warm across switches. `needs_docker = False`. Blocking `WhisperModel.transcribe()` runs in `loop.run_in_executor` to keep the asyncio loop responsive.

`create_transcriber(config, *, device, language, auto_detect_language)` dispatches:
- CPU + `inprocess_supported=True` → `LocalWhisperTranscriber`
- CPU + `requires_gpu=True` → raises with a clear "switch models" error
- Otherwise → protocol-based dispatch (`realtime_ws` or `openai_http`)

Device resolution: `resolve_device("auto")` probes `ctranslate2.get_cuda_device_count()`. CTranslate2 ships with faster-whisper, so no extra detection dep. Active model set in `config.toml` under `[inference].model`; device under `[inference].device` (`auto`/`cuda`/`cpu`). Both switchable at runtime via the dashboard.

## vLLM Realtime Protocol

The WebSocket protocol requires a specific commit sequence:

1. Receive `session.created` from server
2. Send `session.update` with model name
3. **Send non-final `input_audio_buffer.commit`** — this starts the generation engine
4. Send `input_audio_buffer.append` chunks (base64 PCM16)
5. Send `input_audio_buffer.commit` with `final: true` — signals end of audio

Without step 3, the server never starts processing audio. This was a hard-won bug fix.

**Logprobs are not available** in the realtime WebSocket protocol. The `transcription.done` message only returns token counts in the `usage` dict (`prompt_tokens`, `completion_tokens`, `total_tokens`). The `transcription.delta` messages only contain `type` and `delta` (text). Confidence extraction code exists in `TranscriptionResult` and will activate automatically if vLLM adds logprob support.

## SendInput INPUT Struct

The ctypes `INPUT` struct union **must** include `MOUSEINPUT` (not just `KEYBDINPUT`) so that `ctypes.sizeof(INPUT)` returns 40 bytes on 64-bit Windows. With only `KEYBDINPUT` the struct is 32 bytes, and `SendInput` silently returns 0 — no error code, no exception. The modifier-release step (`_release_all_modifiers`) and configurable `injection_delay_ms` are also required because the push-to-talk hotkey (Ctrl+Shift+Alt) can leave stale modifier state. `inject_text` first polls `GetAsyncKeyState` until the user has physically released all modifiers, because the hotkey deactivates on the *first* modifier going up. **Never wrap the Ctrl+V `SendInput` in `AttachThreadInput`**: SendInput does not need it, and detaching right after the call re-syncs the target thread's key state while it may still be processing the V keydown — the app then reads Ctrl as up and types a literal "v" (intermittent). `_paste_via_message` uses `GetGUIThreadInfo` to find the focused control for the same reason.

## Correction Dictionary

`dictionary.py` — `apply_dictionary(text, entries)` runs on the raw transcription after the nexus/assistant checks and before voice commands. Entries (`wrong` → `right`) live in the `dictionary_entries` table, edited in the dashboard Settings tab. Matching is whole-word, case-insensitive, whitespace-tolerant; longest `wrong` phrase wins; `right` is inserted verbatim. `suggest_entries(original, corrected)` diffs a review correction token-wise (case-sensitive, so "cloud code" → "Claude Code" stays one phrase) and returns only `replace` blocks — insertions/deletions are usually fillers, not misheard terms.

## Voice Commands

`voice_commands.py` converts spoken phrases into characters/transforms before text injection. Runs after nexus check, only if `[voice_commands].enabled = true` in config.

- **Structural** (always active): `new line`, `new paragraph`, `tab`, `tabulator`, `all caps`
- **Symbols** (opt-in via `symbols` list): `slash`, `backslash`, `pipe`, `tilde`, `asterisk`, `open/close paren/bracket/brace`, `less/greater than` (safe defaults) + `hash`, `percent`, `dash`, `hyphen`, `plus`, `equal`, `colon`, `star` (ambiguous, disabled by default)
- **Numbers** (opt-in via `numbers_as_digits = true`): spoken number words → digits, including compounds ("twenty five" → "25", "one thousand five hundred" → "1500")

Symbol commands do not break `all caps` mode; structural commands do. Pattern is compiled per active-symbol set and cached via `lru_cache`. `ALL_SYMBOL_INFO` (public) is consumed by the dashboard UI to render per-symbol toggle chips.

## Nexus OS Command Layer

`os_commands.py` + `window_manager.py` — checked before voice commands. If transcription matches a nexus pattern, executes the command and skips text injection entirely.

- Window management: `"nexus <action> <app>"` — open, close, focus, fullscreen, minimize, snap left/right
- Requires `os_commands.enabled = true` and app registered under `[os_commands.apps]` in `config.toml`
- Force-foreground uses AttachThreadInput + Alt key trick + SPI timeout fallback (Vista+ restriction workaround)
- **"nexus flag"** is always available regardless of `os_commands.enabled` — flags most recent transcription for review, no DB record for the flag itself

## Assistant Forward

`assistant.py` — checked after the nexus flag/OS commands, before voice commands, only when `[assistant].enabled`. `"nexus assistant <text>"` (also `assistent`, an optional comma/colon) sends `send <service> <text>` to `host:port` over TCP, one request per connection, client shuts its write side, reply is one line. Saved to the DB as `[assistant] <text>`, nothing injected. `OSError` (nothing listening) plays `beep_error` and falls through to normal injection, so a sentence is never lost.

## Translate Before Inject

`translator.py` — `translate(text, config)` runs on the processed text (after dictionary and voice commands) right before injection, only when `[translator].enabled`. It POSTs `{"text": ...}` to `[translator].url` - the personal-tooling `translator` server, TranslateGemma via Ollama on 127.0.0.1:8003, which translates German sentences and passes English ones through byte for byte - and injects the reply. The transcript is saved as spoken, never translated. Any failure (nothing listening, non-200, malformed reply, timeout) logs a warning and returns the original text, so a sentence is never lost. The tray item "Translate to English: ON/OFF" flips `enabled` and saves the config. Blocking HTTP, run in the executor like the injection. `translate()` returns a `TranslateResult` (text, translated, source, ms, error); the last four are saved on the transcription row (`translated`, `translate_source`, `translate_ms`, `translate_error`) so the dashboard overview can report how often translation ran, its average server time, and how often the fallback fired.

## Audio Persistence

Each recording saved as 16kHz mono WAV in the configurable `audio/` directory alongside the database. Enables batch review with playback in the dashboard Review/Edit tabs.
