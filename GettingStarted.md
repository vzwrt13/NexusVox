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

That runs Whisper in-process. The model downloads from HuggingFace on first use (~150 MB for `whisper-small`, ~1.5 GB for `whisper-large-v3-turbo`) and caches under `%USERPROFILE%\.cache\huggingface`.

To stay on CPU even if you later add a GPU, open the dashboard (`http://localhost:47392`) → Settings → **Compute Device** → set to `CPU`.

---

## Prerequisites

- **Windows 11** (uses Windows-specific keyboard hooks and SendInput API)
- **Python 3.11+**

**CPU path** — no additional requirements.

**Optional, both paths — [ffmpeg](https://ffmpeg.org/) on your `PATH`:**
Only the dashboard's Upload tab needs it, and only for non-WAV formats (MP3, FLAC, OGG, WEBM). Push-to-talk dictation and WAV uploads work without ffmpeg. Without it, a non-WAV upload fails with `Audio conversion failed`.

```powershell
winget install Gyan.FFmpeg    # or: choco install ffmpeg
ffmpeg -version               # verify — open a new terminal first so PATH is picked up
```

**GPU path** (Voxtral / Cohere / Parakeet, plus faster Whisper):
- **NVIDIA GPU** with CUDA runtime installed (VRAM depends on model — see table below)
- **Docker Desktop** with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- **Hugging Face account** with an access token ([create one here](https://huggingface.co/settings/tokens)) — only for Cohere Transcribe, the one gated model. Voxtral and Parakeet download without a token.

---

## Choosing a Backend

| Model | Hugging Face ID | Model license | Runtime | Resources | Docker? |
|---|---|---|---|---|---|
| Whisper Small (CPU-optimized) | `Systran/faster-whisper-small` | MIT | in-process | CPU, ~500 MB RAM | **No** |
| Whisper Base (CPU) | `Systran/faster-whisper-base` | MIT | in-process | CPU, ~300 MB RAM | **No** |
| Whisper Medium (CPU) | `Systran/faster-whisper-medium` | MIT | in-process | CPU, ~1.5 GB RAM | **No** |
| Distil-Whisper Medium.en (EN, CPU) | `Systran/faster-distil-whisper-medium.en` | MIT | in-process | CPU, ~800 MB RAM | **No** |
| Distil-Whisper Large V3 (EN, CPU) | `Systran/faster-distil-whisper-large-v3` | MIT | in-process | CPU, ~1.5 GB RAM | **No** |
| Whisper Large V3 Turbo | `deepdml/faster-whisper-large-v3-turbo-ct2` | MIT | in-process (CPU) / Docker (GPU) | CPU or ~4 GB VRAM | **No** on CPU |
| Parakeet TDT 0.6B | `nvidia/parakeet-tdt-0.6b-v3` | CC-BY-4.0 ² | Docker | ~2 GB VRAM | Yes |
| Cohere Transcribe | `CohereLabs/cohere-transcribe-03-2026` | Apache-2.0 ¹ | Docker | ~6 GB VRAM | Yes |
| Voxtral Mini 4B | `mistralai/Voxtral-Mini-4B-Realtime-2602` | Apache-2.0 | Docker | ≥16 GB VRAM | Yes |

¹ **Gated on Hugging Face** — requires accepting the terms on the model page plus an `HF_TOKEN` (see step 1.1). It is the only model that needs a token.
² **Attribution required** — CC-BY-4.0 obliges you to credit NVIDIA if you redistribute the model.

Model licenses are separate from NexusVox's own AGPL-3.0 license; no model weights ship with NexusVox. Check the terms of the model you pick, especially for commercial use.

**Whisper Small** is the fastest CPU option — use it for push-to-talk on a laptop. **Whisper Base** is smaller and faster still, at lower accuracy. **Whisper Medium** is the best multilingual CPU option including German. The **Distil-Whisper** variants are English-only but noticeably faster than their full counterparts. **Whisper Large V3 Turbo** is higher-accuracy on CPU but several times slower; on GPU it's the best balance. **Voxtral** offers real-time WebSocket streaming with the highest quality if you have a 16 GB+ GPU.

---

## 1. Start the Inference Server (Docker — GPU users only)

> **CPU / Whisper users: skip this section.** Whisper runs in-process — no Docker container needed.

### 1.1 Configure the Hugging Face Token

> Required only for **Cohere Transcribe**, the one gated model. Voxtral and Parakeet download automatically without a token — skip this step if you are not using Cohere.

```bash
cd docker
cp .env.example .env
```

Open `docker/.env` and replace the placeholder with your token:

```
HF_TOKEN=hf_your_actual_token_here
```

Before the first Cohere run, open the [Cohere Transcribe 03-2026](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026) model page while logged in and accept the terms — the model is gated, and the download fails without it even with a valid token.

### 1.2 Build and Start the Container

Use `--profile` to select your model:

```bash
cd docker
docker compose --profile parakeet up --build  # Parakeet (HTTP, port 8002) -- default
docker compose --profile voxtral up --build   # Voxtral (WebSocket, port 8000)
docker compose --profile cohere up --build    # Cohere (HTTP, port 8001)
```

The first start will download the model and cache it in a Docker volume. Subsequent starts are fast.

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

---

## 3. Configure

On first run a `config.toml` is created automatically from the example template. You can also copy it manually:

```bash
cp src/nexusvox/config.example.toml config.toml
```

### Configuration Reference

```toml
[general]
# Language for transcription: "en" or "de"
language = "en"
# Delay in ms between releasing modifier keys and sending Ctrl+V paste
injection_delay_ms = 500
# Enable automatic language detection per recording (experimental)
auto_language_detection = false
# Enable voice commands for text formatting (new line, new paragraph, tab, all caps)
voice_commands_enabled = true

[hotkey]
# Push-to-talk: hold all listed modifiers simultaneously to record
# Options: ctrl, shift, alt, win
modifiers = ["ctrl", "shift", "alt"]

[audio]
# Sample rate in Hz (must match model expectation)
sample_rate = 16000
# Audio chunk size in bytes for WebSocket streaming
chunk_size = 4096

[inference]
# Server URL — used by Docker-backed models; ignored when Whisper runs in-process on CPU.
server_url = "ws://localhost:8000/v1/realtime"
# Transcription delay in ms (multiples of 80, range 80-2400)
transcription_delay_ms = 480
# Transcription model. In-process on CPU (no Docker): "whisper-small", "whisper-base",
# "whisper-medium", "whisper-large-v3-turbo", "distil-whisper-large-v3" (EN),
# "distil-whisper-medium-en" (EN).
# Require NVIDIA GPU + Docker: "voxtral-mini-4b", "cohere-transcribe", "parakeet-tdt-0.6b".
model = "voxtral-mini-4b"
# Compute device: "auto" (detect CUDA, else CPU), "cuda", or "cpu".
# Use "cpu" to force in-process Whisper — useful for benchmarking on a GPU machine.
device = "auto"
# Optional compute-type override for faster-whisper. Defaults: int8 on CPU, float16 on GPU.
# compute_type = "int8"

[database]
# SQLite database path (relative to config file or absolute)
path = "nexusvox.db"
# Directory for saved audio recordings (relative to database path)
audio_dir = "audio"

[os_commands]
# Enable "nexus" voice commands for OS-level window management
enabled = false

[os_commands.apps]
# Map app aliases to executable paths. Use the alias in voice commands.
# chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe"
# discord = "C:/Users/YourUser/AppData/Local/Discord/Update.exe"

[voice_commands]
# Enable voice command text formatting substitutions
enabled = true
# Transcribe numbers as digits ("five" → "5") instead of words
numbers_as_digits = false
# When true, spoken symbol keywords bypass voice command processing and are injected literally
bypass_symbols = false
# Active symbol keywords (spoken words that insert punctuation)
symbols = ["period", "comma", "question mark", "exclamation mark"]
```

The defaults work out of the box — you only need to edit `config.toml` if you want to change the model, language, or tweak audio/inference settings.

---

## 4. Run

Make sure the Docker inference server is running (step 1), then:

```bash
python -m nexusvox
```

You should see:

```
NexusVox started. Hold Ctrl+Shift+Alt to talk.
```

A system tray icon also appears, where you can toggle the language or quit the app. Open the dashboard at `http://localhost:47392` for analytics, settings, and transcription history.

---

## 5. Usage

1. **Hold** `Ctrl + Shift + Alt` — recording starts (you'll hear a beep)
2. **Speak** into your microphone
3. **Release** any of the modifier keys — recording stops (second beep)
4. The transcribed text is automatically pasted at your cursor position

> NexusVox uses the clipboard to inject text (Ctrl+V). Your previous clipboard content is saved and restored after each injection.

### Voice Commands

With `voice_commands_enabled = true`, certain spoken phrases are converted before injection:

| Say | Result |
|---|---|
| "new line" | `\n` |
| "new paragraph" | `\n\n` |
| "tab" | `\t` |
| "all caps [text]" | `TEXT` |
| "period", "comma", etc. | `.`, `,`, etc. (configurable via `symbols`) |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `Connection refused` on start | Make sure the Docker container is running and healthy. Check `docker compose logs`. |
| No audio captured | Verify your default microphone is set correctly in Windows Sound settings. |
| Model download fails | Only Cohere Transcribe is gated: check your `HF_TOKEN` in `docker/.env` and make sure you accepted the terms on its Hugging Face model page. Other models need no token — a failure there is usually network or disk space. |
| High GPU memory usage | The default `--gpu-memory-utilization 0.70` reserves 70% of VRAM. Lower it in `docker-compose.yml` if needed. |
| Text not appearing at cursor | Some applications block simulated Ctrl+V. Try a standard text editor first. |
| `Audio conversion failed` on upload | ffmpeg is missing from your `PATH`. Install it (`winget install Gyan.FFmpeg`), then restart NexusVox. WAV files upload without ffmpeg. |

---

## Project Structure

```
NexusVox/
├── pyproject.toml              # Project metadata & dependencies
├── config.toml                 # Your local configuration (git-ignored)
├── docker/
│   ├── Dockerfile              # vLLM image (Voxtral/Cohere)
│   ├── Dockerfile.parakeet     # NeMo-based Parakeet image
│   ├── Dockerfile.whisper      # faster-whisper image
│   ├── docker-compose.yml      # Container orchestration (4 profiles)
│   ├── parakeet_server.py      # FastAPI wrapper for Parakeet
│   ├── whisper_server.py       # FastAPI wrapper for Whisper
│   ├── .env.example            # HF_TOKEN template
│   └── .env                    # Your HF token (git-ignored)
└── src/nexusvox/
    ├── config.example.toml     # Configuration template, shipped inside the package
    ├── __main__.py             # Entry point
    ├── app.py                  # Main application orchestrator
    ├── audio.py                # Microphone capture (16kHz PCM16)
    ├── config.py               # Configuration loader
    ├── db.py                   # Database session management
    ├── docker_ctl.py           # Docker Compose profile control (start/stop/health-check)
    ├── feedback.py             # Audio beep feedback
    ├── file_transcribe.py      # Audio file upload conversion and transcription
    ├── hotkey.py               # Global push-to-talk hotkey listener
    ├── injector.py             # Text injection via clipboard + SendInput
    ├── lang_detect.py          # Language detection
    ├── models.py               # SQLAlchemy models
    ├── os_commands.py          # Nexus OS command parsing and dispatch
    ├── transcriber.py          # Transcriber ABC, factory, and model implementations
    ├── tray.py                 # System tray icon & menu
    ├── voice_commands.py       # Voice command text formatting substitutions
    ├── window_manager.py       # Win32 window management functions
    └── dashboard/
        ├── __init__.py         # Flask app and route definitions
        ├── analytics.py        # Database queries for dashboard stats
        ├── api.py              # Flask API endpoints
        └── static/
            ├── index.html      # Dashboard UI
            ├── style.css       # Styles
            ├── dashboard.js    # Frontend logic
            └── chart.min.js    # Bundled Chart.js
```
