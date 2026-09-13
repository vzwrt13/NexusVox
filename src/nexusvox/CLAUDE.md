# NexusVox — Core Runtime

## Hotkey Modes

`hotkey.py` — `HotkeyListener` does all of its work inside pynput's `win32_event_filter`, i.e. synchronously in the WH_KEYBOARD_LL hook on raw virtual-key codes (`_handle(vk, pressed) -> swallow?`), because that is the only place an event can still be suppressed (`Listener.suppress_event()`). The binding is `[hotkey].modifiers` plus an optional `[hotkey].key` (names → VK codes in `config.HOTKEY_KEY_VKS`; validated by `validate_hotkey`, an unusable binding in the file falls back to the default). Both the binding and `config.mode` are re-read from the shared config on every event, so the dashboard is live (`_sync_binding` resets the pressed-state tracking, keeps `_active`). Left and right modifier variants both count. Edge detection via `_combo_down`: a *complete press* is the last missing modifier going down (modifier-only binding) or the key going down while all modifiers are down (the key must come last, so "hold A, then Ctrl" never records); releasing any key of the combo resets it. Hold: complete press activates, first key up deactivates. Toggle: each complete press flips `_active`; releases do nothing. Swallowing rule: modifiers are always passed through; the key-down that completes the combo is swallowed and `_key_swallowed` then swallows every event of that key (auto-repeats, the final key-up) until it is physically released — so in hold mode the letter is dead for the whole recording (even after the modifiers went up), and in toggle mode it types again the moment the user lets go; a key pressed without all modifiers is plain typing. Do not filter injected events (PowerToys/AutoHotkey remaps arrive injected). `active` is the recording intent in both modes and is what `app.py` consults before the cycle's `finally` release of the mic guard. `still_recording()` is the mic-guard watchdog check: physical key state (`GetAsyncKeyState`, modifiers and key) in hold mode, `_active` in toggle mode, because in toggle mode the keys are up while recording. Mode switch mid-recording: hold→toggle keeps recording until the next press; toggle→hold stops on the next key release. `describe_hotkey()` renders the binding for logs.

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

`translator.py` — `translate(text, config)` runs on the processed text (after dictionary and voice commands) right before injection, only when `[translator].enabled`. It POSTs `{"text": ...}` to `[translator].url` - the personal-tooling `translator` server, TranslateGemma via Ollama on 127.0.0.1:8003, which translates German sentences and passes English ones through byte for byte - and injects the reply. The transcript is saved as spoken, never translated. Any failure (nothing listening, non-200, malformed reply, timeout) logs a warning and returns the original text, so a sentence is never lost. The tray item "Translate to English (local server): ON/OFF" flips `enabled` and saves the config. `is_reachable(config)` POSTs an empty text with a 1.5 s cap; any HTTP answer counts, and the dashboard shows the result under the toggle (`/api/settings/translator-status`) so the toggle never looks self-contained. The contract is documented for users in `docs/TRANSLATOR.md`. Blocking HTTP, run in the executor like the injection. `translate()` returns a `TranslateResult` (text, translated, source, ms, error); the last four are saved on the transcription row (`translated`, `translate_source`, `translate_ms`, `translate_error`) so the dashboard overview can report how often translation ran, its average server time, and how often the fallback fired.

## Mic Guard

`mic_guard.py` — opt-in via `[mic_guard].enabled` (dashboard Settings > Voice Input toggle; the guard is always constructed and `app.py` checks the flag on every hold, so the toggle is live). On key-down every WASAPI audio session on the default *capture* endpoint (pycaw's `AudioUtilities` helpers only look at the render endpoint, so `enumerate_capture_sessions` asks `IMMDeviceEnumerator` for `eCapture` explicitly) that is not PID 0 and not our own PID is muted through `ISimpleAudioVolume`, but only if it was unmuted; on key-up those sessions are set back to unmuted. Never the endpoint device. NexusVox is a single process, so `os.getpid()` is the whole exclusion. `MicGuard` is pure logic over a `CaptureSession` protocol (tests use fakes); `WasapiCaptureSession` is the real thing. Sessions are re-enumerated on every hold and release and matched by `GetSessionInstanceIdentifier`, so no COM pointer ever crosses threads and COM is initialised per thread (`_ensure_com`, MTA, `RPC_E_CHANGED_MODE` tolerated). `SetMute` costs ~40 ms per session and pynput runs the hotkey callbacks inside the low-level keyboard hook, so `app.py` calls `hold_async()` / `release_async()`: one worker thread, FIFO, so a very short tap still muted-then-restores in order; the record-start event is set at the same time, dictation start is not delayed. Nothing may stay muted: `release()` is idempotent and runs on key-up, in the `finally` of `_transcription_cycle`, in `_shutdown`, via `atexit`, and from a watchdog (`watchdog_s`, default 30 s) that only fires when `HotkeyListener.modifiers_physically_down()` (GetAsyncKeyState, independent of the hook) says the keys are up — a long dictation re-arms it. During a hold the muted sessions are in `mic_guard_state.json` next to the database; `restore_leftovers()` runs at startup and matches on the stable `GetSessionIdentifier` too, because Windows persists per-app mute across app restarts. Known gaps: an app that starts capturing mid-hold is not muted until the next hold, and **Discord ignores session mute entirely** (verified 2026-09-13: mute flag set, Discord's voice activity ring still lights; an ordinary WASAPI/MME capture stream and a Chrome getUserMedia stream both go to exactly 0.0 within 300 ms, so browser calls are covered). The supported answer for Discord is its own *Push to Mute* keybind on the NexusVox hotkey; say so in any user-facing text about this feature.

## Audio Persistence

Each recording saved as 16kHz mono WAV in the configurable `audio/` directory alongside the database, unless `[database].store_audio` is false (`App._store_audio` checks the flag per recording, so the dashboard toggle is live; the transcript row is saved either way with `audio_path` NULL, which the Review queue already filters out). Enables batch review with playback in the dashboard Review/Edit tabs.
