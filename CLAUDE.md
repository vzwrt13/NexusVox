# NexusVox — Claude Code Project Guide

## Language Policy — English Only (non-negotiable)

**Every artifact belonging to this project is written in English. No exceptions.**

This covers, without limitation:

- Source code — identifiers, comments, docstrings, log messages, exception text
- User-facing strings — dashboard UI, tray menu, CLI output, error messages
- Configuration — keys, values, and the comments in `config.example.toml`
- Documentation — README, GettingStarted, CONTRIBUTING, `docs/`, every `CLAUDE.md`
- Product and marketing material — `product/`, `landing/`
- Git — commit messages, branch names, tags, release notes, PR and issue text

**The conversation language is independent of the artifact language.** The maintainer
frequently prompts in German. That never changes the output: a German prompt still
produces English code, English commits, and English documentation. Do not mirror the
prompt language into the repository, and do not ask whether an exception applies —
there is none.

Rationale: this is a public open-source project. A mixed-language codebase excludes
contributors and makes the project look unmaintained.

The single deliberate exception is `.open-source-docs/`, a git-ignored, one-off
migration log that never ships and is retired once the repo migration is complete. It is
the only place German may remain; no other file may follow its example.

## Bilingual Speech — English and German (non-negotiable)

Anything that matches spoken words (voice commands, number words, OS/assistant triggers, dictionary
helpers) must handle **English and German from the first version**, with tests for both. Both
languages are always recognised; the matcher never depends on the `[general].language` setting.

## Quick Reference

```bash
# Start inference server (requires NVIDIA GPU + Docker)
# Use --profile to select model: parakeet (default), voxtral, cohere, or whisper
cd docker && docker compose --profile parakeet up --build

# Install app (Python 3.11+)
pip install -e ".[dev]"

# Run
python -m nexusvox

# Lint (before pushing)
ruff check src/
ruff format --check src/

# Check CI status
gh run list
```

## Architecture

Hotkey listener (pynput low-level hook; default Ctrl+Shift+Alt, any modifiers optionally ending in one key under `[hotkey].key`, whose keystrokes are swallowed while held as part of the combo; `[hotkey].mode` = `hold` push-to-talk or `toggle` press-to-start/press-to-stop; `[hotkey].paste_key` (default `V`) tapped during a recording drops it and pastes the last pasted text again; `[hotkey].polish_key` (default off) makes the recording a spoken instruction for the selected text, rewritten by a local language model (`polish.py`, `llm.py`, `[llm]`: Ollama by default; a spoken preset name from `polish_presets` in SQLite stands for its stored instruction, `polish_presets.py`); binding and mode switchable live in the dashboard) triggers microphone capture (sounddevice, 16kHz PCM16 mono) and, when `[mic_guard]` is enabled, mutes every other app's WASAPI capture session for the duration of the hold (`mic_guard.py`); the audio streams to a transcription backend (WebSocket for Voxtral/vLLM, HTTP POST for Cohere/Parakeet). A recording marked as a command - `[os_control].command_key` (default `Y`, so Ctrl+Shift+Alt+Y; `OEM_102` for the `<` key) pressed during a recording while every hotkey modifier is down, and swallowed (without the full chord it types normally) - is never typed: its transcript goes to the Controller as `send os-control <text>` (`::show` already goes out on the key press, from a thread so the hook never blocks; `::hide` instead of the text when the transcript is empty, and also when the cycle is skipped or fails), is saved as `[os-control] <text>`, and an unreachable port or an `error:` reply plays the error beep. Every other transcribed text passes through a nexus OS command check, then the assistant forward (`assistant.py`: "nexus assistant <text>" is sent to a local TCP port as `send <service> <text>` and not typed; unreachable port falls back to typing), then the correction dictionary (`dictionary.py`, entries in SQLite, edited in the dashboard), then snippets (`snippets.py`: user-defined trigger phrases from SQLite, swapped for placeholders so voice commands and the translator never touch the stored text, expanded right before injection), then optional voice command processing, then optional auto cleanup (`cleanup.py`, `[cleanup].level` `light`/`medium`: the [llm] model drops fillers and fixes punctuation; an implausible reply or any failure types the text unchanged), then optional translation into English (`translator.py`: a local HTTP translator server; unreachable server falls back to the original text), then is injected at the cursor (with `[feedback]` the cycle is signalled by chimes, a click-through waveform overlay at the bottom centre of the screen (`overlay.py`), or both) via clipboard + SendInput (Ctrl+V) with WM_PASTE fallback (`clipboard.py` keeps those clipboard writes out of the Win+V history), and saved to SQLite as spoken, before translation. Commands (nexus flag, nexus open, assistant, nexus OS commands) are dispatched by `App._run_command`; everything from the dictionary to translation (cleanup included) is the pure function `pipeline.process_text`, which returns what to type, what to save and whether the paste can be learned from. With `[learn_from_edits]` enabled, `edit_watch.py` then reads the focused field through UI Automation for a while and turns the words the user corrects there into pending dictionary suggestions (accepted in the dashboard). With `[remote_mic]` enabled (and a token set), `remote_mic.py` serves a WebSocket on port 47393 (`/v1/remote-mic`, `Authorization: Bearer <token>`): a phone sends `start`, PCM16 frames and `stop`, and the cycle runs with `RemoteAudioSource` in place of `AudioCapture` (`_transcription_cycle(remote=True)`: never a command recording, no nexus/assistant actions, hotkey ignored meanwhile; the processed text is not injected but sent back in `done` for the phone to insert); contract in `docs/REMOTE_MIC.md`. A Flask dashboard on port 47392 provides analytics, settings, flagging/review, and file upload transcription (optional speaker detection with Parakeet: `diarization.py`, the container's `/v1/audio/diarization`). System tray (waveform mark, red while recording) opens the dashboard on left-click; the right-click menu has language toggle, translate toggle, dashboard, and quit. The dashboard Settings tab has a "Voice Input" card (hotkey binding with a Record button, hotkey mode, mic guard) and mirrors language, auto detection, and translate in a "Language & Translation" card.

*Module-specific details auto-load from subdirectory CLAUDE.md files: `src/nexusvox/CLAUDE.md` (transcribers, protocols, SendInput, nexus), `src/nexusvox/dashboard/CLAUDE.md` (Flask routes, API endpoints, tabs), `.github/CLAUDE.md` (CI/CD, releases).*

## Key Constraints

- **Windows-only** — uses Win32 SendInput + WM_PASTE for text injection and pynput win32 hooks
- **NVIDIA GPU required only for server-backed models** (Voxtral/Cohere, and Parakeet/Whisper in their Docker builds). CPU-only mode runs faster-whisper or Parakeet (`onnx-asr`, ONNX int8, ~670 MB, no speaker detection) in-process — no Docker, no GPU. Set `[inference].device = "cpu"` in config.toml.
- **HF_TOKEN** — required in `docker/.env` only for the Docker/GPU path
- **ffmpeg required** — must be on system PATH for pydub to convert non-WAV audio uploads (WAV works without it). Documented for users in README Requirements and GettingStarted Prerequisites.

## Configuration

`config.toml` (git-ignored) is created in the working directory on first run, from the template at `src/nexusvox/config.example.toml`. The template ships inside the package so an installed wheel can find it; do not move it back to the repository root. Editable settings: language, auto language detection (bool), console log level (`[general].log_level`, `DEBUG`/`INFO`/`WARNING`/`ERROR`, default `INFO`, applied at start to the `nexusvox` loggers), injection delay (ms between modifier release and paste), hotkey modifiers, optional key, mode (`hold`/`toggle`) paste key and polish key, audio sample rate/chunk size, inference server URL, transcription model (10 entries in `MODEL_REGISTRY`: GPU/Docker `voxtral-mini-4b`, `cohere-transcribe`, `parakeet-tdt-0.6b` (also in-process on CPU through ONNX); in-process CPU `whisper-large-v3-turbo`, `whisper-small`, `whisper-base`, `whisper-medium`, `distil-whisper-large-v3`, `distil-whisper-medium-en`; `custom-server`, a user-run server under `[inference.custom]`: url, protocol `http`/`realtime`, model_name — no Docker, contract in `docs/CUSTOM_SERVER.md`), transcription delay, database path, audio storage directory (default `audio/`) and `store_audio` (bool, default true; off saves the transcript only), OS commands enabled (bool, default false), OS commands apps (name→exe path mapping under `[os_commands.apps]`), assistant forward (`[assistant]` section: enabled bool, host, port, service, timeout_s), os-control command recordings (`[os_control]` section: enabled bool, command_key, host, port, service, timeout_s), voice commands (`[voice_commands]` section: enabled bool, numbers_as_digits bool, structural list + bypass_structural bool, symbols list + bypass_symbols bool, press_enter bool), translate before inject (`[translator]` section: enabled bool, url, timeout_s), mic guard (`[mic_guard]` section: enabled bool, watchdog_s), dictation feedback (`[feedback].mode` `sound`/`visual`/`both`), learn from edits (`[learn_from_edits]` section: enabled bool, window_s), the language model for polish (`[llm]` section: url, protocol `ollama`/`openai`, model, api_key, timeout_s, keep_alive), polish in terminals (`[polish]` section: in_terminals bool, default false), auto cleanup (`[cleanup]` section: level `off`/`light`/`medium`, timeout_s), remote mic (`[remote_mic]` section: enabled bool, host, port, token), meeting recording (`[meeting]` section: modifiers, key, mic_device, output_device, consent_hint bool, leave_dictation_out bool).

## Linting

Ruff is configured in `pyproject.toml` under `[tool.ruff]`:

- **Target**: Python 3.11
- **Line length**: 120
- **Rules**: E (pycodestyle errors), W (warnings), F (pyflakes), I (isort), UP (pyupgrade), B (bugbear)
- Auto-fix safe issues: `ruff check --fix src/`
- Auto-format: `ruff format src/`

## Tests

Tests covering config, database, analytics, Flask API, TranscriptionResult, transcriber factory/implementations, Docker orchestration, language detection, file upload transcription (conversion + endpoints), and nexus OS command parsing/dispatch/dashboard endpoints. Uses in-memory SQLite for isolation. See `docs/TESTING.md` for the full guide.

## Backlog

`BACKLOG.md` in the repository root tracks planned work, known issues, and priorities. It is a
local, git-ignored working document and is deliberately not part of the published repository —
do not add links to it from files that ship. After completing a feature or fix, update it: move
the item to the Resolved section and strike it through in its original list.
