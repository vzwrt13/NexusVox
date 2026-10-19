# NexusVox — Dashboard

## Server

Flask server starts once on first tray click (left-click on the icon or the Dashboard menu item), runs in a daemon thread on `127.0.0.1:47392`. Static files in `src/nexusvox/dashboard/static/` (HTML, CSS, JS, bundled Chart.js — no CDN). API endpoints under `/api/*` return JSON, consumed by vanilla JS `fetch()` calls.

- `analytics.py` — all SQLAlchemy query functions
- `api.py` — Flask route wrappers around analytics queries
- `benchmarks.py` — benchmark JSON file I/O and cross-model comparison (Dev tab)

## Look

Aurora Precision theme, shared with the website. Tokens are the `:root` variables in `style.css` (`--bg`, `--surface*`, `--border*`, `--text*`, `--accent`/`--accent-light`, `--green`, `--red`, `--orange`, 18px cards); `COLORS` at the top of `dashboard.js` reads them (and the font) from the computed style at startup for Chart.js, so `style.css` is the only place to change a color; chart-only tints are the `--accent-soft`, `--accent-fill` and `--green-fill` tokens. Geist and Geist Mono (SIL OFL) are bundled flat in `static/` (`Geist-Variable.woff2`, `GeistMono-Variable.woff2`, license in `Geist-OFL.txt`; `pyproject.toml` packages `dashboard/static/*`, so they must stay flat) - no CDN, the dashboard works offline. The `<header>` is a left sidebar (brand + stacked `.tab` buttons) and stacks above the content below 800px; `.tab` / `.tab-content` / `data-tab` are what `activateTab` in `dashboard.js` keys on, so keep them.

**No sideways scrolling, from 360px up, on every tab.** `document.documentElement.scrollWidth` must never exceed `innerWidth`. The rules that keep it so: `.setting-row` wraps (label above, control below); controls and grids use `max-width: 100%`, `min-width: 0` or `minmax(min(Npx, 100%), 1fr)` instead of a fixed minimum; `main` sets `overflow-wrap: anywhere`, inherited, so long unbroken strings (model names, paths, file names, `<code>`, status lines) wrap and flex items shrink - do not override it; a table wider than a phone sits in a `.table-wrap` (`overflow-x: auto`) and keeps `overflow-wrap: normal`. A new row, grid or table follows the same rules. There are no UI tests: check by loading the dashboard in iframes of 360, 480, 640, 800 and 1280px in headless Edge, opening every tab, with deliberately long model names and file names in the data.

## Tabs

**Analytics (tab 1)** — stat cards (transcriptions, words dictated, avg WPM with min/max, avg and longest duration, transcribe time with real-time factor, release-to-text latency, translated, minutes saved), charts (timeline, language, peak hours, latency trend, top words, heatmap) and a per-model table (`GET /api/model-breakdown`). Timing comes from `transcribe_ms` / `latency_ms` on `Transcription`, both measured from the moment recording stopped; rows written before those columns existed are skipped in the averages. The `confidence` column still exists but no backend fills it (vLLM's realtime protocol sends no logprobs), so it is not shown.

**Settings (tab 2)**
- Voice Input card: Hotkey editor (keycap display of the current binding with a `default`/`custom` badge, the default named in the text, modifier checkboxes + key select, a *Reset to default* button (disabled while on the default, `hotkey_default` in `GET /api/settings`) and a *Record* button that captures the next combo pressed in the page via `KeyboardEvent.code`; a modifier-only combo is taken on the first key-up), `POST /api/settings/hotkey` with `{modifiers: [...], key: ""}` (400 with `error` on an unusable binding, canonical modifier order and upper-case key in the reply); Hotkey Mode select (`hold` / `toggle`), `POST /api/settings/hotkey-mode`; Paste Last Text Key select (`Off` or any hotkey key name, `paste_key` in `GET /api/settings`), `POST /api/settings/paste-key` with `{key}` (400 on an unknown key, the hotkey's own key, the enabled os-control command key, or the polish key - the listener would ignore or misread those); Polish Selected Text Key select (`polish_key`, `POST /api/settings/polish-key`, same checks against the paste key) with an LLM status line (`GET /api/settings/llm-status`, probed live like the translator), a Polish In Terminals Too toggle (`polish_in_terminals`, `POST /api/settings/polish-terminals`, `[polish].in_terminals`) a Polish Model input (`llm_model`, `POST /api/settings/llm-model`; warms the new model up while cleanup is on) and an Auto Cleanup select (`cleanup_level`, `cleanup_levels`, `POST /api/settings/cleanup-level` with `{level}`, 400 on an unknown level; switching it on warms the model up in the background); the Mute Other Apps While Recording toggle, `POST /api/settings/mic-guard`, and the Store Audio Recordings toggle, `POST /api/settings/store-audio` (`[database].store_audio`). All live: `HotkeyListener` reads the shared `Config` on every key event and `app.py` checks `[mic_guard].enabled` on every hold, no restart and no app bridge needed.
- Compute Device dropdown (`Auto`/`GPU (CUDA)`/`CPU`): writes `[inference].device` to `config.toml`; requires app restart. On CPU, GPU-only models are filtered out of the model dropdown server-side.
- Model switcher dropdown: for GPU-backed models, selecting stops the old Docker container, starts the new one, waits for health check, reconnects the transcriber. For in-process Whisper (CPU), just reloads the model — no Docker calls. JS polls `GET /api/models/status` every 2s for spinner/status updates.
- Correction Dictionary card: add/remove entries; Learn From Edits toggle (`POST /api/settings/learn-from-edits`, `learn_from_edits` in `GET /api/settings`) with a status line under it - the last paste's outcome and the counts since start (`GET /api/settings/learn-from-edits-status`, `EditWatcher.status()` passed in as `get_learn_status`; without the app it answers no counts; refreshed every 3 s while the Settings tab is shown and the page visible, and a new `suggested` count reloads the suggestion list) and the pending suggestions learned from edits after a paste, each with Add (`POST /api/dictionary/suggestions/<id>/accept`) and dismiss (`DELETE /api/dictionary/suggestions/<id>`).
- Snippets card: trigger input, multi-line text and an options box (`name = text` per line), Add/Save (`POST /api/snippets` with `trigger`, `expansion`, `options` as `[{name, text}]` parsed from the text box in `dashboard.js`, `id` set to edit: `Database.update_snippet`, else `add_snippet`, which replaces a snippet with the same trigger), *Fill from GitHub* (`GET /api/snippets/github-repos`, runs `gh repo list`, appends `name = url` lines), per-row Edit and remove (`DELETE /api/snippets/<id>`); a trigger already used by another snippet is rejected, and a snippet needs a text or at least one option.
- Polish Presets card: name, instruction and a *Default when nothing is said* checkbox, Add/Save (`POST /api/polish-presets` with `name`, `instruction`, `is_default`, `id` set to edit; 400 when the name is taken (any case, Unicode case-folded), has more than 5 words (the spoken matcher scores 5) or a field is blank; making one the default clears the old default), per-row Edit and remove (`DELETE /api/polish-presets/<id>`).
- Nexus OS Commands card: toggle enable/disable, lists supported actions and registered apps.
- Voice Commands card: master enable/disable toggle; Numbers as Digits toggle; Press Enter Command toggle (`POST /api/voice-commands/press-enter`); Bypass Structural Commands toggle + structural command chips (`new line`, `new paragraph`, `tab`, `all caps`; `POST /api/voice-commands/structural`); Bypass Symbol Commands toggle + symbol command chips (click to activate/deactivate individual symbols, persisted immediately via `POST /api/voice-commands/symbols`).

**Edit (tab 3)** — flagged transcriptions with editable correction textarea and audio playback. Saves via `POST /api/flagged/<id>/correct`. Audio served via `GET /api/audio/<tid>`.

**Upload (tab 4)** — drag-and-drop file upload (WAV, MP3, FLAC, OGG, WebM; max 25 MB) with transcription history list. Conversion + transcription logic in `file_transcribe.py`; 60-second chunking to avoid GPU OOM; results in `file_transcriptions` SQLite table (`FileTranscription` model in `models.py`). No voice commands or text injection for file transcriptions. With Parakeet (`word_timestamps` in `MODEL_REGISTRY`) an upload also asks the server for per-word timing; `transcribe_file(..., word_timestamps=True)` (only the upload passes it; Review Retry does not) shifts each chunk's words by the real length of the audio before it, the joined list is stored as JSON in `file_transcriptions.words` (NULL for other engines), and a chunk that timed out or came back without words is logged and returned as `notes` in the `POST /api/file-transcribe` reply, which the upload tab shows in its own non-error `#upload-note` box (the upload is saved); Review Retry saves nothing, so a note there fails the retry with the note as the error. A *Detect speakers* toggle above the drop zone (off by default) adds `diarize=1` to the upload FormData; `refreshDiarizeToggle` reads `diarization` from `GET /api/models/current` whenever the tab opens and disables the toggle with the hint "Requires Parakeet as the active model" otherwise. The flag goes through `upload_and_transcribe(diarize=)` to `transcribe_file`; the labelled `Speaker N: ...` paragraphs are the saved `text` (shown with `white-space: pre-wrap`, `.upload-text`) and each stored word gets `speaker`. A problem with speaker detection is a note, the upload still saves. Display or export of the word timings themselves is not built yet.

**Review (tab 5)** — unreviewed recordings that have audio files, oldest first. Each card has *Retry* (`POST /api/review/<tid>/retry`): the stored WAV is transcribed again with the current model (same path as file upload, `transcribe_file`) and the result goes into the correction box; nothing is saved until the user submits. It needs the WAV, i.e. `[database].store_audio`, which the tab says. Users confirm correctness via checkbox or provide corrected text. Flagged-but-unreviewed items highlighted red with checkbox defaulting to incorrect. `reviewed` column on `Transcription` is tri-state: `0`=unreviewed, `1`=correct, `2`=incorrect.

**Dev (tab 6, dev-only)** — hidden unless `?dev` query param is present. Reads benchmark JSON files from `benchmarks/` directory. Two views: comparison mode (table + WER bar chart + RTF bar chart across all benchmarks) and detail mode (WER distribution histogram, latency scatter plot, sample explorer with word-level diff). WER targets from roadmap: EN <8%, DE <12%. Orange accent distinguishes from production tabs. Backend logic in `benchmarks.py`.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings` | Language, auto detection, translate, hotkey mode + modifiers + key (+ the allowed modifier/key options) |
| POST | `/api/settings/hotkey` | Set the binding `{modifiers, key}` (400 on an unusable one) |
| POST | `/api/settings/hotkey-mode` | Set `hold` or `toggle` (400 on anything else) |
| POST | `/api/settings/mic-guard` | Toggle muting other apps' microphone sessions while recording |
| POST | `/api/settings/store-audio` | Toggle keeping a WAV per recording (`[database].store_audio`) |
| GET | `/api/models` | List available models (filtered: GPU-only hidden on CPU systems) |
| GET | `/api/models/current` | Active model, and `diarization`: whether it can detect speakers in uploads |
| POST | `/api/models/switch` | Switch model |
| GET | `/api/models/status` | Switch progress (polled every 2s) |
| GET | `/api/device` | Current device: requested, resolved, cuda_available |
| POST | `/api/device` | Set requested device (`auto`/`cuda`/`cpu`) — requires app restart |
| GET | `/api/voice-commands` | Voice commands config + all symbol info |
| POST | `/api/voice-commands/enabled` | Toggle voice commands |
| POST | `/api/voice-commands/numbers` | Toggle numbers-as-digits |
| POST | `/api/voice-commands/press-enter` | Toggle the trailing "press enter" command |
| POST | `/api/voice-commands/symbols` | Update active symbol list |
| POST | `/api/voice-commands/bypass-symbols` | Toggle bypass symbol commands |
| POST | `/api/voice-commands/structural` | Update active structural command list |
| POST | `/api/voice-commands/bypass-structural` | Toggle bypass structural commands |
| GET | `/api/os-commands` | OS commands config |
| POST | `/api/os-commands/enabled` | Toggle OS commands |
| POST | `/api/os-commands/apps` | Update registered apps |
| GET | `/api/flagged/<id>` | Get flagged transcription |
| POST | `/api/flagged/<id>/correct` | Save correction |
| GET | `/api/audio/<tid>` | Serve WAV audio file |
| POST | `/api/file-transcribe` | Upload + transcribe audio file (form field `diarize=1` labels speakers) |
| GET | `/api/file-transcriptions` | List file transcription results |
| GET | `/api/review` | Page of unreviewed recordings, newest first (`?before_id=` cursor; returns `items` + `remaining`) |
| POST | `/api/review/<tid>/retry` | Transcribe the stored audio again with the current model (`ok`, `text`, `model`; nothing saved) |
| POST | `/api/settings/polish-key` | Set the polish-selected-text key `{key}` (`""` = off; 400 on a clash) |
| POST | `/api/settings/cleanup-level` | Set auto cleanup `{level}`: `off`, `light`, `medium` (400 otherwise) |
| POST | `/api/settings/polish-terminals` | Toggle polish in terminals `{enabled}` (`[polish].in_terminals`) |
| POST | `/api/settings/llm-model` | Set the `[llm]` model name `{model}` (400 when blank) |
| GET | `/api/settings/llm-status` | `url`, `model`, `reachable` of the `[llm]` server |
| POST | `/api/settings/paste-key` | Set the paste-last-text key `{key}` (`""` = off; 400 on an unknown key) |
| POST | `/api/review/<tid>` | Submit review (correct/incorrect + correction); returns dictionary `suggestions` diffed from the correction |
| GET | `/api/dictionary` | List correction-dictionary entries |
| POST | `/api/dictionary` | Add or update an entry (`wrong`, `right`) |
| DELETE | `/api/dictionary/<id>` | Remove an entry |
| GET | `/api/snippets` | List snippets (sorted by trigger) |
| POST | `/api/snippets` | Add, or update with `id` (`trigger`, `expansion`, `options` as `[{name, text}]`) |
| GET | `/api/snippets/github-repos` | The user's GitHub repos as options `[{name, text: url}]` (GitHub CLI) |
| DELETE | `/api/snippets/<id>` | Remove a snippet |
| GET | `/api/polish-presets` | List polish presets (sorted by name) |
| POST | `/api/polish-presets` | Add, or update with `id` (`name`, `instruction`, `is_default`; 400 on a clash or blank field) |
| DELETE | `/api/polish-presets/<id>` | Remove a polish preset |
| GET | `/api/dictionary/suggestions` | Pending suggestions learned from edits |
| POST | `/api/dictionary/suggestions/<id>/accept` | Turn a suggestion into an entry |
| DELETE | `/api/dictionary/suggestions/<id>` | Dismiss a suggestion |
| POST | `/api/settings/learn-from-edits` | Toggle learning from edits after a paste |
| GET | `/api/settings/learn-from-edits-status` | How the pastes since start ended for learning: `{counts, last: {outcome, message, at}}` |
| GET | `/api/benchmarks` | List benchmark files with summary stats |
| GET | `/api/benchmarks/compare` | Cross-model comparison with pass/fail |
| GET | `/api/benchmarks/<filename>` | Full benchmark data (summary + samples) |

## Docker Orchestration

`docker_ctl.py` — `stop_profile`, `start_profile`, `wait_for_healthy`. Thread→async bridge uses `asyncio.run_coroutine_threadsafe`.
