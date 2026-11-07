# NexusVox — Getting Started

Local speech-to-text with push-to-talk and auto-insert. Hold a hotkey, speak, release — your words appear at the cursor.

**An NVIDIA GPU is strongly recommended.** The CPU-only path below needs no Docker and no GPU, but it is slow enough that dictation stops feeling immediate — see the Performance table in [README.md](README.md#performance) before committing to it.

---

## CPU-only install (no Docker, no NVIDIA) — fallback

If you have an NVIDIA GPU, skip this section and start at [Prerequisites](#prerequisites); the GPU path is both faster and more accurate. This section is for machines that have no other option.

Be aware of what you are trading away: on CPU, Whisper Medium reaches GPU-grade accuracy but needs about 6.8 seconds for a 10-second utterance, while Whisper Small answers in about 2 seconds at roughly twice the German error rate. The numbers are in the [Performance table](README.md#performance).

That said, the setup really is this short:

```bash
pip install -e .
python -m nexusvox
```

That runs the model in-process — Parakeet (ONNX, int8) by default, Whisper if you pick it. The model downloads from HuggingFace on first use (~670 MB for Parakeet, ~1.6 GB for `whisper-large-v3-turbo`) and caches under `%USERPROFILE%\.cache\huggingface`; the dashboard's Settings tab shows the download while it runs. Parakeet on CPU has no speaker detection for uploads — that needs the GPU container.

To stay on CPU even if you later add a GPU, open the dashboard from the tray icon → Settings → **Advanced** → **Compute Device** → set to `CPU`.

---

## Prerequisites

- **Windows 11** (uses Windows-specific keyboard hooks and SendInput API)
- **Python 3.11+**

**CPU path** — no additional requirements.

**Optional, both paths — [ffmpeg](https://ffmpeg.org/) on your `PATH`:**
Only uploads in the dashboard's Recordings tab need it, and only for non-WAV formats (MP3, FLAC, OGG, WEBM). Push-to-talk dictation and WAV uploads work without ffmpeg. Without it, a non-WAV upload fails with `Audio conversion failed`.

```powershell
winget install Gyan.FFmpeg    # or: choco install ffmpeg
ffmpeg -version               # verify — open a new terminal first so PATH is picked up
```

**GPU path** (Parakeet, plus faster Whisper):
- **NVIDIA GPU** with CUDA runtime installed (VRAM depends on model — see table below)
- **Docker Desktop** with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

---

## Choosing a Backend

| Model | Hugging Face ID | Model license | Runtime | Resources | Docker? |
|---|---|---|---|---|---|
| Whisper Large V3 Turbo | `deepdml/faster-whisper-large-v3-turbo-ct2` | MIT | in-process (CPU) / Docker (GPU) | CPU or ~4 GB VRAM | **No** on CPU |
| Parakeet TDT 0.6B | `nvidia/parakeet-tdt-0.6b-v3` | CC-BY-4.0 ¹ | Docker (GPU) / in-process ONNX int8 (CPU) | ~2 GB VRAM, or CPU (~670 MB download) | Yes on GPU |
| Custom server ² | — | your server's | your own server | none on this PC | **No** |

¹ **Attribution required** — CC-BY-4.0 obliges you to credit NVIDIA if you redistribute the model.
² **Your own backend** — a server you run that speaks the OpenAI-compatible HTTP or realtime WebSocket protocol, set under `[inference.custom]`. Unlike every other entry, audio leaves this PC if that server is remote. Setup and protocol contract: [docs/CUSTOM_SERVER.md](docs/CUSTOM_SERVER.md).

Model licenses are separate from NexusVox's own PolyForm Noncommercial 1.0.0 license; no model weights ship with NexusVox. Check the terms of the model you pick, especially for commercial use.

**Parakeet** is the default: the fastest and most accurate of the measured models, 25 European languages with automatic detection. **Whisper Large V3 Turbo** covers 99 languages — pick it for a language Parakeet does not know; on CPU it is several times slower than Parakeet, on a GPU it is fast. The dashboard marks each model as downloaded or not, with its size. A config that still names a removed model (Voxtral, Cohere, the smaller Whisper sizes, Distil-Whisper) starts on Parakeet.

---

## 1. Start the Inference Server (Docker — GPU users only)

> **CPU users: skip this section.** Parakeet and Whisper run in-process — no Docker container needed.

### 1.1 Build and Start the Container

Use `--profile` to select your model:

```bash
cd docker
docker compose --profile parakeet up --build  # Parakeet (HTTP, port 8002) -- default
docker compose --profile whisper up --build   # Whisper Large V3 Turbo (HTTP, port 8003)
```

The first start will download the model and cache it in a Docker volume. Subsequent starts are fast.

The Parakeet container also serves speaker detection for uploads in the dashboard's Recordings tab (*Detect speakers*, up to 4 speakers). Its model downloads on the first upload that uses it, so that upload takes a little longer. A Parakeet image built before speaker detection existed has an older NeMo and must be rebuilt once with `--build`.

Wait until the server reports it is ready. The default Parakeet endpoint will be available at `http://localhost:8002/v1/audio/transcriptions`.

> To run the container in the background, add `-d`:
> ```bash
> docker compose --profile parakeet up --build -d
> ```

---

## 2. Install NexusVox

From the project root:

```bash
pip install -e .
```

This installs NexusVox in editable/development mode along with all dependencies.

For development (includes pytest):

```bash
pip install -e ".[dev]"
```

To run the plain app while an extension package is installed, set `NEXUSVOX_NO_EXTENSIONS=1` before
starting it (details in [docs/EXTENSIONS.md](docs/EXTENSIONS.md)).

---

## 3. Configure

On first run a `config.toml` is created automatically from the example template. You can also copy it manually:

```bash
cp src/nexusvox/config.example.toml config.toml
```

### Configuration Reference

```toml
[general]
# Delay in ms between releasing modifier keys and sending Ctrl+V paste
injection_delay_ms = 500
# How much the console log says: "DEBUG", "INFO", "WARNING" or "ERROR" (takes effect on the next start)
log_level = "INFO"
# The log is also written to %LOCALAPPDATA%\NexusVox\logs (one file per day, 7 days kept).
# Dictated text is shortened there; true writes it in full (debugging only)
log_full_text = false
# Enable voice commands for text formatting (new line, new paragraph, tab, all caps)
voice_commands_enabled = true

[hotkey]
# Press all listed modifiers simultaneously to record
# Options: ctrl, shift, alt, win (may be empty when a key is set)
modifiers = ["ctrl", "win"]
# Optional key that completes the combo (letter, digit, F1-F24, SPACE, ...), e.g. "a"
# for Ctrl+Win+A. Swallowed while held as part of the combo, so it never types.
key = ""
# "hold": record while the keys are held (push-to-talk)
# "toggle": press once to start, press again to stop
mode = "hold"

[audio]
# Sample rate in Hz (must match model expectation)
sample_rate = 16000
# Audio chunk size in bytes for WebSocket streaming
chunk_size = 4096

[inference]
# Server URL — used by Docker-backed models; ignored when Whisper runs in-process on CPU.
server_url = "http://localhost:8002/v1/audio/transcriptions"
# Transcription delay in ms (multiples of 80, range 80-2400)
transcription_delay_ms = 480
# Transcription model: "parakeet-tdt-0.6b" (default; in-process on CPU or a Docker container on a GPU)
# or "whisper-large-v3-turbo" (99 languages; same two ways to run).
# Your own server (see [inference.custom] below): "custom-server".
model = "parakeet-tdt-0.6b"
# Compute device: "auto" (detect CUDA, else CPU), "cuda", or "cpu".
# Use "cpu" to force in-process Whisper — useful for benchmarking on a GPU machine.
device = "auto"
# Optional compute-type override for faster-whisper. Defaults: int8 on CPU, float16 on GPU.
# compute_type = "int8"

[inference.custom]
# Your own transcription server, used when model = "custom-server" (docs/CUSTOM_SERVER.md).
url = ""
protocol = "http"             # "http" or "realtime"
model_name = "custom-server"

[database]
# SQLite database path (relative to config file or absolute)
path = "nexusvox.db"
# Directory for saved audio recordings (relative to database path)
audio_dir = "audio"
# Keep a WAV file of every recording (needed for playback in the Review tab)
store_audio = true

[voice_commands]
# Enable voice command text formatting substitutions
enabled = true
# Transcribe numbers as digits ("five" / "fünf" → "5"); on by default, set false to keep words
numbers_as_digits = true
# Structural commands (new line, new paragraph, tab, all caps) are their own category:
# pick which ones fire, or bypass them all without clearing the list
structural = ["new paragraph", "new line", "tab", "all caps"]
bypass_structural = false
# End a dictation with "press enter" / "Enter drücken" to paste, then press Enter (off by default)
press_enter = false
# When true, spoken symbol keywords bypass voice command processing and are injected literally
bypass_symbols = false
# Active symbol keywords (spoken words that insert punctuation)
symbols = ["period", "comma", "question mark", "exclamation mark"]
```

The defaults work out of the box — you only need to edit `config.toml` if you want to change the model or tweak audio/inference settings.

---

## 4. Run

Make sure the Docker inference server is running (step 1), then:

```bash
python -m nexusvox
```

You should see:

```
NexusVox started. Hold Ctrl+Win to talk.
```

A system tray icon also appears, where you can toggle translation or quit the app. Open the dashboard from the tray icon (left-click) for analytics, settings, and transcription history.

---

## 5. Usage

1. **Hold** `Ctrl + Win` — recording starts (you'll hear a beep, or see a small waveform at the bottom of the screen if you chose visual feedback)
2. **Speak** into your microphone
3. **Release** any of the modifier keys — recording stops (second beep)
4. The transcribed text is automatically pasted at your cursor position

To paste the last text again, hold `Ctrl + Win`, tap `V`, let go: the recording is dropped and the text NexusVox pasted last is pasted once more (key in Settings → Text & Polish, `[hotkey].paste_key`; empty turns it off).

### Cleanup and Polish need Ollama

Dictation works on its own. Auto Cleanup, Polish and the upload cleanup use a local language model, and NexusVox does not ship one. If Ollama is missing, Settings shows a red hint under *Auto Cleanup* and *Polish Selected Text Key* (only when you turned them on), in the *Models* card, and under *Clean up text* in the upload dialog of the Recordings tab (only with that option on). If `[llm].url` points to another PC, the hint shows that address instead of an install link:

1. Install Ollama from [ollama.com/download](https://ollama.com/download) and start it (it runs in the tray).
2. Open Settings → Models. If Ollama is running but the chosen model is missing, the hint says so and a *Download* button fetches it after you confirm. You can also pick another model from the list (models that are not installed are greyed out).
3. Wait a few seconds. The hint disappears on its own while Settings is open; no restart is needed.

To rewrite selected text by voice, pick a *Polish Selected Text Key* in Settings → Text & Polish (e.g. `P`), install [Ollama](https://ollama.com) and run `ollama pull gemma4:e4b` (`gemma4:e2b` on a PC without an NVIDIA GPU: that is the model NexusVox asks for there unless you set `[llm].model`). Then select text in any app, hold `Ctrl + Win`, tap `P`, say what to do ("make it shorter", "mach es formeller", "translate to English") or nothing, and let go: the selection is replaced by the rewritten text (Ctrl+Z undoes it). The model shares the GPU with the speech model - the dashboard shows whether the model server answers. Other servers (LM Studio, llama.cpp, vLLM, a cloud API) work through `[llm]` with `protocol = "openai"`. Terminals are skipped by default, because a terminal does not report its selection and polish would have to send Ctrl+C, which stops the running program when nothing is selected. Turn on *Polish In Terminals Too* (Settings → Advanced) (`[polish].in_terminals`) to polish there as well; the rewritten text is then pasted at the prompt.

Instructions you use often go into Settings → Vocabulary → *Polish Presets*: a name such as `prompt engineer` and the instruction, e.g. "Rewrite this as a clear, specific prompt for an AI assistant: state the goal, the context and the expected output." Then say just the name after tapping the polish key; words after it are added ("prompt engineer, in English"). Close spellings match, names can be in any language, and the preset marked *Default* is used when you say nothing.

To tidy every dictation, set *Auto Cleanup* in Settings → Text & Polish (or `[cleanup].level`) to *Light* (fillers such as "um" and "äh", false starts and repeated words go, punctuation is fixed, your wording stays) or *Medium* (also grammar, slightly more concise). It uses the same model server as polish and adds about half a second per dictation. NexusVox loads the model at start; while it is still loading, or when the model's reply looks like an answer or a rewrite rather than your words cleaned up, the dictation is typed unchanged. Dictations under three words are not sent, and the transcript is saved as you said it.

Prefer not to keep the keys down? Switch **Hotkey Mode** to *Toggle* in the dashboard's Settings tab (or `mode = "toggle"` in `config.toml`): press `Ctrl + Win` once to start, press it again to stop. The change takes effect immediately.

> NexusVox uses the clipboard to inject text (Ctrl+V). Your previous clipboard content is saved and restored after each injection.

### Voice Commands

With `[voice_commands].enabled = true`, certain spoken phrases are converted before injection. English and German are always both recognised:

| Say | Result |
|---|---|
| "new line" / "neue Zeile" | `\n` |
| "new paragraph" / "neuer Absatz" | `\n\n` |
| "tab" / "Tabulator" | `\t` |
| "all caps [text]" / "alles groß [text]" | `TEXT` |
| "twenty five" / "fünfundzwanzig" (with `numbers_as_digits`) | `25` |
| "period", "comma", etc. | `.`, `,`, etc. (configurable via `symbols`) |
| "[text], press enter" / "[Text], Enter drücken" (with `press_enter`) | pastes `text`, then presses Enter |

The press enter command only fires at the very end of a dictation ("press enter to continue" stays text). Also accepted: "hit enter", "drück Enter", "drücke Enter". Turn it on in the dashboard under Settings → Text & Polish → Voice Commands → Press Enter Command.

### Learn From Edits

Turn on Settings → Vocabulary → Correction Dictionary → Learn From Edits (or `[learn_from_edits].enabled`). After each paste NexusVox reads the text field for 20 seconds (`window_s`); fix a misheard word there and it shows up below the toggle as a suggestion to add or dismiss - nothing enters the dictionary on its own. Password fields and terminals are never read (terminals expose no readable text), and nothing is learned from a dictation that auto cleanup or translation changed. The line under the toggle says how the last paste ended, so you can tell "nothing corrected" from "this app cannot be read". The console log (`[general].log_level`, `INFO` by default) names the reason for every paste that taught nothing: the field could not be read, the edit was too small, there were too many changes, no word was replaced, and whether the watch ended because the time window ran out or the next dictation arrived. If a read fails (a browser rebuilds its address bar, for example) NexusVox fetches the focused field again before it gives up, and the last text it saw is still compared.

---

## Troubleshooting

NexusVox writes its log to `%LOCALAPPDATA%\NexusVox\logs` (one file per day, the last 7 days are kept); Settings → Privacy → *Open log folder* opens it. Send the newest file when you report a problem. Dictated text is shortened in the file to its first 20 characters and its length, unless `[general].log_full_text` is on.

| Problem | Solution |
|---|---|
| `Connection refused` on start | Make sure the Docker container is running and healthy. Check `docker compose logs`. |
| No audio captured | Verify your default microphone is set correctly in Windows Sound settings. |
| Model download fails | No model needs a token, so a failure is usually network or disk space. |
| Text not appearing at cursor | Some applications block simulated Ctrl+V. Try a standard text editor first. |
| `Audio conversion failed` on upload | ffmpeg is missing from your `PATH`. Install it (`winget install Gyan.FFmpeg`), then restart NexusVox. WAV files upload without ffmpeg. |
| Starting NexusVox again while it runs | The second start opens the running instance's dashboard and exits; there is only ever one tray icon and one hotkey hook. |
| The dashboard does not open | Port 47392 is held by another program. NexusVox then uses the next free port in 47400-47409 and logs a warning; if all are held it shows a message and logs the reason. Dictation keeps working. |

---

## Project Structure

```
NexusVox/
├── pyproject.toml              # Project metadata & dependencies
├── config.toml                 # Your local configuration (git-ignored)
├── docker/
│   ├── Dockerfile.parakeet     # NeMo-based Parakeet image
│   ├── Dockerfile.whisper      # faster-whisper image
│   ├── docker-compose.yml      # Container orchestration (4 profiles)
│   ├── parakeet_server.py      # FastAPI wrapper for Parakeet
│   └── whisper_server.py       # FastAPI wrapper for Whisper
└── src/nexusvox/
    ├── __main__.py             # Entry point
    ├── config.example.toml     # Configuration template, shipped inside the package
    ├── config.py               # Configuration loader
    ├── extensions.py           # Extension interface
    ├── file_cleanup.py         # Transcript cleanup for uploaded files
    ├── llm.py                  # Language model client
    ├── snippets.py             # Snippet matcher (for extensions) and fuzzy name matching
    ├── app/
    │   ├── core.py             # Main application orchestrator: start, main loop, shutdown
    │   ├── commands.py         # Extension hooks, assistant and nexus commands
    │   ├── common.py           # Logger and constant the app modules share
    │   ├── cycle.py            # One transcription cycle: record, transcribe, type
    │   ├── events.py           # Hotkey, phone and os-control callbacks
    │   ├── feedback.py         # Chimes and overlay cues of a dictation
    │   ├── models.py           # Model switching and warm-up
    │   ├── polish.py           # Paste key and Polish on the selected text
    │   └── records.py          # Saved rows, audio files and statistics
    ├── audio/
    │   ├── capture.py          # Microphone capture (16kHz PCM16)
    │   ├── mic_guard.py        # Mutes other apps' capture sessions while recording
    │   ├── remote_mic.py       # Phone microphone over WebSocket
    │   └── state.py            # Which audio streams are open
    ├── dictation/
    │   ├── dictionary.py       # Correction dictionary matching and suggestions
    │   ├── edit_watch.py       # Learn from edits: UI Automation field watcher
    │   ├── feedback.py         # Audio beep feedback
    │   ├── hotkey.py           # Global push-to-talk hotkey listener
    │   ├── injector.py         # Text injection via clipboard + SendInput
    │   ├── lang_detect.py      # Language detection
    │   ├── os_commands.py      # Nexus OS command parsing and dispatch
    │   ├── overlay.py          # Visual feedback: live waveform at the bottom centre
    │   ├── pipeline.py         # Dictionary to translation: what gets typed and saved
    │   ├── voice_commands.py   # Voice command text formatting substitutions
    │   └── ...                 # assistant, cleanup, clipboard, polish, polish_presets, translator
    ├── meetings/               # Meeting recording and its transcription
    ├── storage/
    │   ├── db.py               # Database session management
    │   ├── models.py           # SQLAlchemy models
    │   └── vocabulary.py       # Plain dictionary and preset records
    ├── system/
    │   ├── docker_ctl.py       # Docker Compose profile control (start/stop/health-check)
    │   ├── tray.py             # System tray icon & menu
    │   ├── window_manager.py   # Win32 window management functions
    │   └── ...                 # com, feedback_form, keyboard, logsetup, paths, single_instance, uia
    ├── transcription/
    │   ├── file_transcribe.py  # Audio file upload conversion and transcription
    │   ├── transcriber.py      # Transcriber ABC, factory, and model implementations
    │   └── ...                 # diarization, exports
    ├── transcribers/           # In-process Parakeet and Whisper backends
    ├── config_sections/        # The config dataclasses, one file per topic
    └── dashboard/
        ├── __init__.py         # Flask app and dashboard server
        ├── analytics*.py       # Database queries for dashboard stats, one file per topic
        ├── api*.py             # The object behind the routes, one mixin per topic
        ├── routes_*.py         # Flask routes, one file per topic
        └── static/
            ├── index.html      # Dashboard UI
            ├── style.css       # Styles
            ├── dashboard-*.js  # Frontend logic, one file per tab plus shared helpers
            └── chart.min.js    # Bundled Chart.js
```
