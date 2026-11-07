# Dashboard API endpoints

The JSON routes the dashboard page calls, by topic in `src/nexusvox/dashboard/routes_*.py`. Rules: `src/nexusvox/dashboard/CLAUDE.md`.

A route reads its JSON body through `_json_object` in `routes_base.py`: valid JSON that is no object is a 400 (`tests/test_dashboard_routes.py` fails on a route that reads it directly). A switch route reads `{enabled}` through `_switch`, in the same module: anything but a boolean is a 400 (`"false"` would otherwise turn the feature on), and a request without the field keeps the route's default.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings` | Translate, hotkey mode + modifiers + key (+ the allowed modifier/key options) |
| POST | `/api/settings/hotkey` | Set the binding `{modifiers, key}` (400 on an unusable one, or on a key the paste, polish or enabled os-control command key uses: the listener would drop that marker silently) |
| POST | `/api/settings/hotkey-mode` | Set `hold` or `toggle` (400 on anything else) |
| POST | `/api/settings/feedback-mode` | Set dictation feedback `{mode}`: `sound`, `visual`, `both` (400 otherwise) |
| POST | `/api/settings/mic-guard` | Toggle muting other apps' microphone sessions while recording |
| POST | `/api/settings/log-full-text` | Toggle full dictated text in the file log `{enabled}` (`[general].log_full_text`, live) |
| POST | `/api/feedback/preview` | The report the Feedback tab would send, as text: `{kind, message, include_log}` -> `{ok, preview, log_included}`; sends nothing; the reply has a `token` for this report. Errors (`{error, message}`): 400 `invalid` (unknown kind, message under 3 characters), 413 `too_large` (message over 5,000), 404 `disabled` (`[support].feedback_form_url` empty, not https, or not on `nexusvox.app`) |
| POST | `/api/feedback/send` | Posts that report to `https://nexusvox.app/api/app-feedback` (host of `feedback_form_url`, only if it is on the allow-list); only the Send click calls it. `{kind, message, include_log[, token]}` -> `{ok, log_included}`; with the `token` of the preview the user saw, that report goes out, 409 `stale` when the inputs changed since. Errors `{error, message[, retry_after]}`: the preview's errors plus 429 `rate` / `daily_cap`, 503 `offline` (no connection), 502 `server` (any other refusal) |
| POST | `/api/log-folder/open` | Open the log folder in Explorer (`{ok, path}`, 500 with `error`) |
| POST | `/api/settings/store-audio` | Toggle keeping a WAV per recording (`[database].store_audio`) |
| GET | `/api/models` | List available models (filtered: GPU-only hidden on CPU systems) |
| GET | `/api/models/current` | Active model, `diarization` (whether it can detect speakers in uploads: the model has it and the running transcriber supports it, so false for Parakeet in-process on CPU), `diarization_models` (display names of the models that can) and `diarization_hint` ("Speaker detection needs the GPU container" when only the CPU engine is the reason, else empty; the upload toggle shows it) |
| POST | `/api/models/switch` | Switch model |
| GET | `/api/models/status` | Switch progress (polled every 2s); `downloading` / `loading` / `error` while an in-process model loads or failed to load, once no switch is in flight |
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
| GET | `/api/features` | The `[experimental]` flags; the page shows `data-feature="<flag>"` elements only when on |
| GET | `/api/os-commands` | OS commands config (only with `[experimental].os_commands`) |
| POST | `/api/os-commands/enabled` | Toggle OS commands |
| POST | `/api/os-commands/apps` | Update registered apps |
| GET | `/api/flagged/<id>` | Get flagged transcription |
| POST | `/api/flagged/<id>/correct` | Save correction |
| GET | `/api/audio/<tid>` | Serve WAV audio file |
| POST | `/api/file-transcribe` | Upload an audio file and start transcribing it in the background (form field `diarize=1` labels speakers, `cleanup=light|medium` also cleans the text up with the [llm] model); 202 `{ok, job_id}`, 400 on a bad file, 409 while another job runs |
| GET | `/api/file-transcribe/<job_id>` | Job status: `state` (`running`/`done`/`error`), `message`, `percent`, `result`, `error` (404 for an unknown id) |
| GET | `/api/file-transcribe/current` | `{job}`: the running job, or the latest unread finished one, else `null` |
| GET | `/api/file-transcriptions` | List file transcription results (`?kind=upload` / `?kind=meeting` narrows them) |
| DELETE | `/api/file-transcriptions/<id>` | Delete an upload or a meeting with its audio files (404 unknown id) |
| GET | `/api/file-transcriptions/<id>/turns` | `{turns: [{speaker, start, end}]}` in seconds, one per speaker paragraph of the text in order (`speaker_turns` over the stored word timing); empty without speaker-labelled words; 404 unknown id. The Recordings tab times its speaker lines with it |
| GET | `/api/meetings/status` | `{recording, elapsed_s, folder}` |
| POST | `/api/meetings/start` | Start recording a meeting (500 with `error` when a device cannot be opened or one runs already) |
| POST | `/api/meetings/stop` | Stop it and start the transcription job: `{ok, job_id, folder}`, or `busy` when another file is being transcribed |
| GET | `/api/meetings/live` | `?after=<n>`: `{enabled, recording, segments: [{n, speaker, label, text, at_s}], status: {active, windows, load, skipped_s, error, count} \| null}` |
| POST | `/api/settings/meeting-live-transcript` | Show the live transcript during a meeting `{enabled}` (default off; applies from the next meeting) |
| GET | `/api/meetings/pending` | Recordings without a transcript `[{name, started_at}]` |
| POST | `/api/meetings/pending/<name>/transcribe` | Transcribe one (409 busy, 404 unknown) |
| DELETE | `/api/meetings/pending/<name>` | Delete one with its audio files (404 unknown) |
| GET | `/api/meetings/devices` | `{inputs, outputs, mic_device, output_device}` |
| POST | `/api/settings/meeting-hotkey` | Set the meeting hotkey `{modifiers, key}` (400 on an unusable one or a clash with dictation) |
| POST | `/api/settings/meeting-devices` | Set `{mic, output}` device names (`""` = default) |
| POST | `/api/settings/meeting-consent-hint` | Show the consent hint before a recording `{enabled}` |
| POST | `/api/settings/meeting-own-voice-label` | Name of the microphone's speaker in meeting transcripts `{label}` (empty = `Speaker 1`; read at transcription time) |
| POST | `/api/settings/meeting-store-audio` | Keep the `meeting.wav` mix after transcription `{enabled}` (default on; read at transcription time; tracks are always deleted after a successful one) |
| POST | `/api/settings/meeting-leave-dictation-out` | Drop dictated words from a meeting transcript `{enabled}` (default on; read at transcription time) |
| GET | `/api/extensions` | Loaded extensions, their dashboard panels (`id`, `title`, `script`) and export targets (`id`, `label`, `extension`, `mimetype`); the built-in `txt`, `srt`, `vtt` exports are always listed, panels and extensions are empty when none is installed |
| GET | `/ext/<panel_id>/<path>` | Static file of an extension's dashboard panel (404 for an unknown panel or file) |
| GET | `/api/file-transcriptions/<id>/audio` | Playable audio kept for a row (a meeting's `meeting.wav` mix; 404 for an upload). `GET /api/file-transcriptions` adds `kind` (`upload`/`meeting`) and `has_audio`; the Recordings detail pane shows its player when there is audio |
| GET | `/api/file-transcriptions/<id>/export/<target_id>` | Download a stored upload through an export target (built-in `txt`, `srt`, `vtt`, or an extension's) (404 unknown id or target, 500 with `error` when the export raises) |
| GET | `/api/review` | Page of unreviewed recordings, newest id first (`?before_id=` cursor; returns `items` + `remaining`) |
| POST | `/api/review/<tid>/retry` | Transcribe the stored audio again with the current model (`ok`, `text`, `model`; nothing saved) |
| POST | `/api/settings/polish-key` | Set the polish-selected-text key `{key}` (`""` = off; 400 on a clash) |
| POST | `/api/settings/cleanup-level` | Set auto cleanup `{level}`: `off`, `light`, `medium` (400 otherwise) |
| POST | `/api/settings/polish-terminals` | Toggle polish in terminals `{enabled}` (`[polish].in_terminals`) |
| POST | `/api/settings/llm-model` | Set the `[llm]` model name `{model}` (400 when blank); warms it up while cleanup is on |
| GET | `/api/settings/llm-models` | Dropdown data: `recommended` (installed flag, `size_gb`, `recommended_here` from the hardware rule in docs/llm-cpu-test.md), `other` installed models from Ollama `/api/tags`, `selected`, `reachable`, `device` and `loaded`: the selected model as Ollama's `/api/ps` reports it (`llm.loaded_model`; `{plausible: true, vram_gb, ram_gb}`, or `{plausible: false, on_gpu}` when `size` is under `PLAUSIBLE_SIZE_RATIO` (0.5) of the size on disk, as Ollama does for gemma4:e2b; null when not loaded). The Models card polls this every 5 s while it is on screen and the page is visible (`createLlmRefresher`), draws only on a change and rebuilds the dropdown only when its options changed |
| GET/POST/DELETE | `/api/settings/llm-pull` | State / start `{model}` / cancel an Ollama `/api/pull` (`llm.ModelPull`: status `idle/pulling/done/error/cancelled`, `completed`, `total`). The dashboard asks for confirmation first and selects the model only after `done` |
| GET | `/api/settings/llm-status` | `url`, `protocol`, `model`, `reachable` of the `[llm]` server and `model_installed` (false: server up, configured model missing; null: unknown). Without `protocol` every non-Ollama server would get the Ollama install link |
| POST | `/api/settings/paste-key` | Set the paste-last-text key `{key}` (`""` = off; 400 on an unknown key) |
| POST | `/api/review/<tid>` | Submit review (correct/incorrect + correction); returns dictionary `suggestions` diffed from the correction |
| GET | `/api/dictionary` | List correction-dictionary entries |
| POST | `/api/dictionary` | Add or update an entry (`wrong`, `right`) |
| DELETE | `/api/dictionary/<id>` | Remove an entry |
| GET | `/api/polish-presets` | List polish presets (sorted by name) |
| POST | `/api/polish-presets` | Add, or update with `id` (`name`, `instruction`, `is_default`; 400 on a clash or blank field) |
| DELETE | `/api/polish-presets/<id>` | Remove a polish preset |
| GET | `/api/dictionary/suggestions` | Pending suggestions learned from edits |
| POST | `/api/dictionary/suggestions/<id>/accept` | Turn a suggestion into an entry |
| DELETE | `/api/dictionary/suggestions/<id>` | Dismiss a suggestion |
| POST | `/api/settings/learn-from-edits` | Toggle learning from edits after a paste |
| GET | `/api/accuracy` | Corrections per 100 words over time (`period`, date range), `most_corrected`, stored `learn` outcomes and `dictionary` usage per entry |
| GET | `/api/settings/learn-from-edits-status` | How the pastes since start ended for learning: `{counts, watched, last: {outcome, message, at}}`; `watched` lists the outcome keys of watches that ran to their end (the rest were skipped; also answered without the app) |
| GET | `/api/benchmarks` | List benchmark files with summary stats |
| GET | `/api/benchmarks/compare` | Cross-model comparison with pass/fail |
| GET | `/api/benchmarks/<filename>` | Full benchmark data (summary + samples) |
