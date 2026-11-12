# NexusVox — Claude Code Project Guide

## Language Policy — English Only (non-negotiable)

**Every artifact belonging to this project is written in English. No exceptions.** This covers source code (identifiers, comments, docstrings, log messages, exception text), user-facing strings (dashboard, tray menu, CLI output, errors), configuration (keys, values, the comments in `config.example.toml`), documentation (README, GettingStarted, CONTRIBUTING, `docs/`, every `CLAUDE.md`), product and marketing material (`product/`, `landing/`) and Git (commit messages, branch names, tags, release notes, PR and issue text).

**The conversation language is independent of the artifact language.** The maintainer frequently prompts in German. That never changes the output: a German prompt still produces English code, commits and documentation. Do not mirror the prompt language into the repository, and do not ask whether an exception applies — there is none. Rationale: this is a public source-available project; a mixed-language codebase excludes contributors and looks unmaintained. Call the licence "source-available", never "open source".

The single deliberate exception is `.open-source-docs/`, a git-ignored, one-off migration log that never ships. No other file may follow its example.

## Bilingual Speech — English and German (non-negotiable)

Anything that matches spoken words (voice commands, number words, OS/assistant triggers, dictionary helpers) must handle **English and German from the first version**, with tests for both. Both languages are always recognised; the matcher never depends on a language setting (there is none: the language of a stored row is detected from its text by `lang_detect.stored_language`: `en`, `de`, or `und` when nothing can be told, which the language chart leaves out).

## Quick Reference

```bash
cd docker && docker compose --profile parakeet up --build   # inference server (NVIDIA GPU + Docker); or --profile whisper
pip install -e ".[dev]"                                     # Python 3.11+
python -m nexusvox                                          # run
ruff check src/ && ruff format --check src/                 # lint before pushing
gh run list                                                 # CI status
```

## Architecture

Hotkey (pynput hook) -> microphone capture -> transcription backend -> `pipeline.process_text` (dictionary, voice commands, cleanup, translation) -> text injected at the cursor and saved to SQLite. A Flask dashboard on port 47392, a system tray, meeting recording, a remote-mic WebSocket and an extension API sit around that path. The whole flow: `docs/internals/architecture.md`. Package layout and the rules for each package: `src/nexusvox/CLAUDE.md`, `src/nexusvox/dashboard/CLAUDE.md`, `.github/CLAUDE.md` (CI/CD, releases). Reference detail for every module is under `docs/internals/`.

## Key Constraints

- **Windows-only** — Win32 SendInput + WM_PASTE for text injection, pynput win32 hooks.
- **NVIDIA GPU only for server-backed models** (Parakeet/Whisper in Docker). `[inference].device = "cpu"` runs faster-whisper or Parakeet (`onnx-asr`, ONNX int8) in-process: no Docker, no GPU.

## Configuration

`config.toml` (git-ignored) is created in the working directory on first run from `src/nexusvox/config.example.toml`. The template ships inside the package so an installed wheel can find it; do not move it back to the repository root. All sections and keys: `docs/internals/configuration.md`.

## Linting And Tests

Ruff, configured in `pyproject.toml`: Python 3.11, line length 120, rules E, W, F, I, UP, B. Fix with `ruff check --fix src/` and `ruff format src/`. Tests use in-memory SQLite; the guide is `docs/TESTING.md`.

## Backlog

`BACKLOG.md` in the repository root tracks planned work, known issues and priorities. It is a local, git-ignored working document and not part of the published repository: do not add links to it from files that ship. After completing a feature or fix, move the item to the Resolved section and strike it through in its original list.
