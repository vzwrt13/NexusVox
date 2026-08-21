# NexusVox — Dashboard

## Server

Flask server starts once on first tray menu click, runs in a daemon thread on `127.0.0.1:47392`. Static files in `src/nexusvox/dashboard/static/` (HTML, CSS, JS, bundled Chart.js — no CDN). API endpoints under `/api/*` return JSON, consumed by vanilla JS `fetch()` calls.

- `analytics.py` — all SQLAlchemy query functions
- `api.py` — Flask route wrappers around analytics queries
- `benchmarks.py` — benchmark JSON file I/O and cross-model comparison (Dev tab)

## Tabs

**Analytics (tab 1)** — Avg Confidence stat card and Confidence Trend chart (empty until vLLM exposes logprobs).

**Settings (tab 2)**
- Compute Device dropdown (`Auto`/`GPU (CUDA)`/`CPU`): writes `[inference].device` to `config.toml`; requires app restart. On CPU, GPU-only models are filtered out of the model dropdown server-side.
- Model switcher dropdown: for GPU-backed models, selecting stops the old Docker container, starts the new one, waits for health check, reconnects the transcriber. For in-process Whisper (CPU), just reloads the model — no Docker calls. JS polls `GET /api/models/status` every 2s for spinner/status updates.
- Nexus OS Commands card: toggle enable/disable, lists supported actions and registered apps.
- Voice Commands card: master enable/disable toggle; Numbers as Digits toggle; symbol command chips (click to activate/deactivate individual symbols, persisted immediately via `POST /api/voice-commands/symbols`).

**Edit (tab 3)** — flagged transcriptions with editable correction textarea and audio playback. Saves via `POST /api/flagged/<id>/correct`. Audio served via `GET /api/audio/<tid>`.

**Upload (tab 4)** — drag-and-drop file upload (WAV, MP3, FLAC, OGG, WebM; max 25 MB) with transcription history list. Conversion + transcription logic in `file_transcribe.py`; 60-second chunking to avoid GPU OOM; results in `file_transcriptions` SQLite table (`FileTranscription` model in `models.py`). No voice commands or text injection for file transcriptions.

**Review (tab 5)** — unreviewed recordings that have audio files, oldest first. Users confirm correctness via checkbox or provide corrected text. Flagged-but-unreviewed items highlighted red with checkbox defaulting to incorrect. `reviewed` column on `Transcription` is tri-state: `0`=unreviewed, `1`=correct, `2`=incorrect.

**Dev (tab 6, dev-only)** — hidden unless `?dev` query param is present. Reads benchmark JSON files from `benchmarks/` directory. Two views: comparison mode (table + WER bar chart + RTF bar chart across all benchmarks) and detail mode (WER distribution histogram, latency scatter plot, sample explorer with word-level diff). WER targets from roadmap: EN <8%, DE <12%. Orange accent distinguishes from production tabs. Backend logic in `benchmarks.py`.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/models` | List available models (filtered: GPU-only hidden on CPU systems) |
| GET | `/api/models/current` | Active model |
| POST | `/api/models/switch` | Switch model |
| GET | `/api/models/status` | Switch progress (polled every 2s) |
| GET | `/api/device` | Current device: requested, resolved, cuda_available |
| POST | `/api/device` | Set requested device (`auto`/`cuda`/`cpu`) — requires app restart |
| GET | `/api/voice-commands` | Voice commands config + all symbol info |
| POST | `/api/voice-commands/enabled` | Toggle voice commands |
| POST | `/api/voice-commands/numbers` | Toggle numbers-as-digits |
| POST | `/api/voice-commands/symbols` | Update active symbol list |
| POST | `/api/voice-commands/bypass-symbols` | Toggle bypass symbol commands |
| GET | `/api/os-commands` | OS commands config |
| POST | `/api/os-commands/enabled` | Toggle OS commands |
| POST | `/api/os-commands/apps` | Update registered apps |
| GET | `/api/flagged/<id>` | Get flagged transcription |
| POST | `/api/flagged/<id>/correct` | Save correction |
| GET | `/api/audio/<tid>` | Serve WAV audio file |
| POST | `/api/file-transcribe` | Upload + transcribe audio file |
| GET | `/api/file-transcriptions` | List file transcription results |
| GET | `/api/review` | List unreviewed recordings |
| POST | `/api/review/<tid>` | Submit review (correct/incorrect + correction) |
| GET | `/api/benchmarks` | List benchmark files with summary stats |
| GET | `/api/benchmarks/compare` | Cross-model comparison with pass/fail |
| GET | `/api/benchmarks/<filename>` | Full benchmark data (summary + samples) |

## Docker Orchestration

`docker_ctl.py` — `stop_profile`, `start_profile`, `wait_for_healthy`. Thread→async bridge uses `asyncio.run_coroutine_threadsafe`.
