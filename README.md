# NexusVox

**Local speech-to-text with push-to-talk and auto-insert.**

Hold a hotkey, speak, release — your words appear at the cursor. No cloud, no subscription, no copy-paste. Runs on CPU out of the box; optional NVIDIA GPU unlocks streaming models.

> **Project status.** NexusVox is published as a reference implementation. It is complete and
> working, but not under active development — no new features are planned. Issues and pull
> requests are welcome; responses may be slow.

---

## Features

- **Push-to-talk** — hold `Ctrl+Shift+Alt`, speak, release; text is injected at the cursor instantly
- **CPU or GPU** — in-process Whisper runs on CPU with zero extra setup; Voxtral / Cohere / Parakeet run on NVIDIA GPUs via Docker
- **Nine transcription models** — six Whisper variants that run in-process on CPU, plus Voxtral (WebSocket streaming), Cohere, and Parakeet on GPU
- **Voice commands** — spoken formatting shortcuts (new line, new paragraph, tab, all caps, punctuation symbols)
- **Nexus OS commands** — window management via voice ("nexus open chrome", "nexus minimize")
- **Flask dashboard** — analytics, settings, transcription history, audio review, and file upload on `http://localhost:47392`
- **Beep feedback** — audio chimes on recording start and stop
- **SQLite history** — every transcription is saved with audio for review and flagging
- **Auto language detection** — per-recording language switching (English/German)

---

## Requirements

- **Windows 11** (Win32 SendInput + pynput hooks)
- **Python 3.11+**

**CPU users** (Whisper only, no Docker):
- No additional dependencies — `pip install` and you're done.

**GPU users** (Voxtral / Cohere / Parakeet, plus faster Whisper):
- **NVIDIA GPU** with CUDA runtime installed (VRAM depends on model — see table below)
- **Docker Desktop** + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

**Optional — [ffmpeg](https://ffmpeg.org/) on your `PATH`:** only needed to upload non-WAV audio files (MP3, FLAC, OGG, WEBM) in the dashboard's Upload tab. WAV uploads and push-to-talk dictation work without it. Install with `winget install Gyan.FFmpeg`, then verify with `ffmpeg -version`.

---

## Quick Start (CPU — no Docker)

```bash
pip install -e .
python -m nexusvox
```

Whisper downloads on first use (~150 MB for `whisper-small`, ~1.5 GB for `whisper-large-v3-turbo`) and caches under `%USERPROFILE%\.cache\huggingface`.

## Quick Start (GPU)

```bash
# 1. Start the inference server for your chosen model
cd docker && docker compose --profile voxtral up --build

# 2. Install
pip install -e .

# 3. Run
python -m nexusvox
```

See [GettingStarted.md](GettingStarted.md) for the full setup guide including configuration, model selection, and troubleshooting.

---

## Model Backends

| Model | Hugging Face ID | Model license | Runtime | Resources | Docker? |
|---|---|---|---|---|---|
| Whisper Small (CPU-optimized) | `Systran/faster-whisper-small` | MIT | in-process | CPU (~500 MB RAM) | **No** |
| Whisper Base (CPU) | `Systran/faster-whisper-base` | MIT | in-process | CPU (~300 MB RAM) | **No** |
| Whisper Medium (CPU) | `Systran/faster-whisper-medium` | MIT | in-process | CPU (~1.5 GB RAM) | **No** |
| Distil-Whisper Medium.en (EN, CPU) | `Systran/faster-distil-whisper-medium.en` | MIT | in-process | CPU (~800 MB RAM) | **No** |
| Distil-Whisper Large V3 (EN, CPU) | `Systran/faster-distil-whisper-large-v3` | MIT | in-process | CPU (~1.5 GB RAM) | **No** |
| Whisper Large V3 Turbo | `deepdml/faster-whisper-large-v3-turbo-ct2` | MIT | in-process (CPU) / Docker (GPU) | CPU or ~4 GB VRAM | **No** on CPU |
| Parakeet TDT 0.6B | `nvidia/parakeet-tdt-0.6b-v3` | CC-BY-4.0 ² | Docker | ~2 GB VRAM | Yes |
| Cohere Transcribe | `CohereLabs/cohere-transcribe-03-2026` | Apache-2.0 ¹ | Docker | ~6 GB VRAM | Yes |
| Voxtral Mini 4B | `mistralai/Voxtral-Mini-4B-Realtime-2602` | Apache-2.0 | Docker | ≥16 GB VRAM | Yes |

¹ **Gated on Hugging Face.** You must accept the terms on the model page and supply an `HF_TOKEN` before the container can download it. All other models download without a token.
² **Attribution required.** CC-BY-4.0 obliges you to credit NVIDIA if you redistribute the model.

Model licenses are **separate from NexusVox's own license** — NexusVox is AGPL-3.0, but no model weights ship with it; each model is downloaded from Hugging Face under its own terms. Check the license of the model you pick, especially for commercial use.

Select your device in the dashboard Settings tab: `Auto` (detect CUDA, fall back to CPU), `GPU (CUDA)`, or `CPU`. On CPU, only the in-process entries are shown.

---

## Dashboard

Open `http://localhost:47392` while NexusVox is running:

| Tab | Description |
|---|---|
| Analytics | Word count, session stats, usage charts |
| Settings | Model switcher, config editor, voice command toggles |
| Edit | Correct transcriptions in-line |
| Upload | Transcribe audio files (WAV, MP3, FLAC, OGG, WEBM — all but WAV need ffmpeg) |
| Review | Flag and replay recordings |

---

## Documentation

- [GettingStarted.md](GettingStarted.md) — full setup guide
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, lint, tests, PR conventions
- [docs/TESTING.md](docs/TESTING.md) — running the test suite

---

## Platform

Windows 11 only. NexusVox uses Win32 APIs (`SendInput`, `WM_PASTE`) for text injection and pynput win32 hooks for the global hotkey listener — these are not portable to macOS or Linux.

---

## Contributing

Bug reports and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup, lint and test commands, and branch conventions. Please read the project status above first, so you know what to expect before spending time on a change.

---

## License

NexusVox is licensed under the **GNU Affero General Public License v3.0 or later** — see [LICENSE](LICENSE).

In practice: use it, study it, change it, run it — privately or inside your company — as much as you like. If you distribute a modified version, or offer one to others over a network, you must make your source available under the same license. That is the whole deal: improvements to NexusVox stay available to everyone.

If those terms don't fit your case, a separate commercial license can be arranged with the copyright holder (NightShift AI GmbH, info@nightshift-ai.de).

[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) covers the bundled Chart.js (MIT) and the licenses of the runtime dependencies.

Speech recognition models are **not** covered by this license. They are downloaded from Hugging Face at runtime under their own terms — see the [model table](#model-backends) above.
