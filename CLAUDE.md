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

Modifier-only hotkey listener (pynput, Ctrl+Shift+Alt) triggers microphone capture (sounddevice, 16kHz PCM16 mono) which streams to a transcription backend (WebSocket for Voxtral/vLLM, HTTP POST for Cohere/Parakeet). Transcribed text passes through a nexus OS command check, then optional voice command processing, then is injected at the cursor via clipboard + SendInput (Ctrl+V) with WM_PASTE fallback, and saved to SQLite. A Flask dashboard on port 47392 provides analytics, settings, flagging/review, and file upload transcription. System tray provides language toggle, dashboard access, and quit.

*Module-specific details auto-load from subdirectory CLAUDE.md files: `src/nexusvox/CLAUDE.md` (transcribers, protocols, SendInput, nexus), `src/nexusvox/dashboard/CLAUDE.md` (Flask routes, API endpoints, tabs), `.github/CLAUDE.md` (CI/CD, releases).*

## Key Constraints

- **Windows-only** — uses Win32 SendInput + WM_PASTE for text injection and pynput win32 hooks
- **NVIDIA GPU required only for server-backed models** (Voxtral/Cohere/Parakeet/Whisper-server via Docker). CPU-only mode runs faster-whisper in-process — no Docker, no GPU. Set `[inference].device = "cpu"` in config.toml.
- **HF_TOKEN** — required in `docker/.env` only for the Docker/GPU path
- **ffmpeg required** — must be on system PATH for pydub to convert non-WAV audio uploads (WAV works without it). Documented for users in README Requirements and GettingStarted Prerequisites.

## Configuration

`config.toml` (git-ignored) is created in the working directory on first run, from the template at `src/nexusvox/config.example.toml`. The template ships inside the package so an installed wheel can find it; do not move it back to the repository root. Editable settings: language, auto language detection (bool), injection delay (ms between modifier release and paste), hotkey modifiers, audio sample rate/chunk size, inference server URL, transcription model (9 entries in `MODEL_REGISTRY`: GPU/Docker `voxtral-mini-4b`, `cohere-transcribe`, `parakeet-tdt-0.6b`; in-process CPU `whisper-large-v3-turbo`, `whisper-small`, `whisper-base`, `whisper-medium`, `distil-whisper-large-v3`, `distil-whisper-medium-en`), transcription delay, database path, audio storage directory (default `audio/`), OS commands enabled (bool, default false), OS commands apps (name→exe path mapping under `[os_commands.apps]`), voice commands (`[voice_commands]` section: enabled bool, numbers_as_digits bool, symbols list of active symbol keywords).

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
