# NexusVox — Core Runtime

Rules and pointers only. How a module works is in `docs/internals/` (table at the end). Module paths such as `app/core.py` are relative to `src/nexusvox/`; paths that start with a top-level folder (`tests/`, `docs/`, `docker/`, `scripts/`, `packaging/`) are from the repository root.

## Package Layout

- Top level: `__main__.py`, `config.py`, `extensions.py`, `llm.py`, `snippets.py`, `file_cleanup.py`. `nexusvox-pro` imports these, so they stay real modules: no shim, because a patch such as `nexusvox.llm.urllib.request.urlopen` has to reach the module that does the work.
- `app/` - `NexusVoxApp` and its mixins (`core`, `events`, `cycle`, `commands`, `polish`, `models`, `records`, `feedback`, `common`).
- `audio/` - `capture.py` (microphone), `state.py`, `mic_guard.py`, `remote_mic.py`.
- `dictation/` - one dictation from hotkey to typed text: `hotkey.py`, `pipeline.py` and its steps (`dictionary.py`, `voice_commands.py`, `cleanup.py`, `translator.py`), `polish.py`, `polish_presets.py`, `injector.py`, `clipboard.py`, `edit_watch.py`, `lang_detect.py`, `assistant.py`, `os_commands.py`, `feedback.py`, `overlay.py`.
- `meetings/` - `recorder.py`, `session.py`, `live.py`, `transcribe.py`.
- `storage/` - `db.py`, `models.py`, `vocabulary.py`.
- `system/` - `paths.py`, `logsetup.py`, `single_instance.py`, `tray.py`, `uia.py`, `window_manager.py`, `keyboard.py`, `com.py`, `docker_ctl.py`, `feedback_form.py`, `feedback_send.py`.
- `transcription/` - `transcriber.py` (base class and factory), `file_transcribe.py`, `diarization.py`, `exports.py`.
- `transcribers/` (in-process backends), `config_sections/` (config dataclasses), `dashboard/` (Flask, own `CLAUDE.md`).

## Structure Rules

- Each package's `__init__.py` is a docstring and nothing else: `nexusvox-pro` installs the core without the audio and GPU dependencies, so importing a package must not pull in its modules.
- `storage/` and `system/` are the lowest layers and import nothing from the packages built on them (type-only imports aside). A type or helper both sides need lives down there.
- Log lines print logger names, so a moved module keeps its old name as a fixed string (`dictation/pipeline.py` logs as `nexusvox.pipeline`, `audio/capture.py` as `nexusvox.audio`, `meetings/live.py` as `nexusvox.meeting_live`, `meetings/transcribe.py` as `nexusvox.meeting_transcribe`, all of `app/` as `nexusvox.app`). A new module uses `logging.getLogger(__name__)`. `tests/test_package_layout.py` pins names, layers, empty `__init__.py` files and Pro's paths.
- `NexusVoxApp` is one class built from mixins; all state lives on the one instance and is set up in `__init__`. Each mixin module imports what it uses, so tests replace those names with `patch_app` (`tests/app_parts.py`), never `monkeypatch.setattr` on `nexusvox.app.core`.
- `config.py` stays the one public import path (`tests/test_config_exports.py` pins it). A new section's dataclass goes into `config_sections/` and is re-exported from `config.py`; a section module never imports `config.py`. `load_config`, `save_config`, `Config`, the hardware probes and `EXAMPLE_CONFIG_PATH` stay in `config.py`, because patches and `__file__` only work there.
- `save_config` has one writer (`_section` / `_toml_value` / `_toml_str`): a new field is a `(key, value)` pair, never a hand-written f-string. It writes atomically (`os.replace`). `tests/test_config.py` walks every string field, so a field the writer misses fails there.
- Settings the dashboard can change are read where they are used (per key event, per dictation), never cached at start, so the toggles stay live.
- Where config, data and models live is decided only in `system/paths.py` (source run versus frozen installed app).

## Dictation Rules

- **Hook:** `dictation/hotkey.py` works synchronously inside pynput's `win32_event_filter` on raw virtual-key codes. Never block in the hook: hand work to a thread. Never filter injected events (remapping tools send them).
- **Order:** nexus check, then the assistant forward, then `pipeline.process_text`: dictionary, press enter, `protect`, voice commands, auto cleanup, translation, `restore`, `on_dictation`. The transcript is saved as spoken. Every optional step falls back to the unchanged text on any failure: a sentence is never lost.
- **Bilingual:** spoken vocabulary covers English and German with tests for both (root `CLAUDE.md`).
- **Injection:** write the clipboard through `clipboard.set_text`, never `pyperclip.copy`. Every `inject_text` caller catches `ClipboardError`. Never wrap the Ctrl+V `SendInput` in `AttachThreadInput`. The ctypes `INPUT` union must include `MOUSEINPUT` (40 bytes on 64-bit), or `SendInput` silently returns 0.
- **Polish and cleanup:** never write the word "polish" in a prompt (small models read it as the language). The language line comes from the selected text, never from the spoken instruction. Hooks of extensions run under `extensions.HOOK_TIMEOUT_S`; a failing hook is skipped.
- **Mic guard:** nothing may stay muted (`release()` is idempotent and runs on every exit path). Discord ignores session mute; user-facing text must point to Discord's Push to Mute.
- **Streaming:** `App._stream_audio` reads the recording to its end marker even when sending fails, so a failed take never leaks into the next.
- **Logging:** spoken text in a log call is wrapped in `Text(...)`; `tests/test_logsetup.py` scans for it. The Feedback tab's log report relies on it, app names of spoken commands included: spoken text outside `Text` would be sent.
- **Learn from edits:** suggestions only; nothing enters the dictionary without the user accepting it in the dashboard.

## Contracts To Keep In Sync

- `docs/CUSTOM_SERVER.md` with both custom-server transcribers (realtime needs the non-final `input_audio_buffer.commit` before the first append; it has no logprobs).
- `docs/EXTENSIONS.md` with `extensions.py`. `API_VERSION` stays 1 for additive hooks and is bumped only on a breaking change; `Extension.api_version` has no default on purpose.
- `docs/CONTROL_PORT.md` (assistant forward and os-control), `docs/TRANSLATOR.md`, `docs/MEETINGS.md`, `docs/REMOTE_MIC.md`, `docs/hotkey-modes.md`.
- `snippets.py` stays a pure matcher with stable signatures (Pro imports it); the core has no snippet storage.

## Reference

| Topic | File |
|-------|------|
| Hotkey: swallowing, markers, cancel, menu mask | `docs/internals/hotkey.md` |
| Polish, auto cleanup | `docs/internals/polish-and-cleanup.md` |
| Dictionary, voice commands, OS commands, assistant, translator, learn from edits | `docs/internals/text-pipeline.md` |
| Transcribers, realtime protocol, diarization, upload cleanup, Docker | `docs/internals/transcription.md` |
| Recording, feedback cues, mic guard, text injection, audio storage | `docs/internals/audio-and-injection.md` |
| Extensions | `docs/internals/extensions.md` |
| Meetings: recorder, transcript, live, session | `docs/internals/meetings.md` |
| App and config layout, file log, data paths, single instance | `docs/internals/runtime.md` |
| Whole flow, config sections | `docs/internals/architecture.md`, `docs/internals/configuration.md` |
