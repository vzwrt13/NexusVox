# NexusVox

[![CI](https://github.com/vzwrt13/NexusVox/actions/workflows/ci.yml/badge.svg)](https://github.com/vzwrt13/NexusVox/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Windows%2011-blue.svg)](#platform)

**Local speech-to-text with push-to-talk and auto-insert.**

Hold a hotkey, speak, release — your words appear at the cursor. No cloud, no subscription, no copy-paste.

**An NVIDIA GPU is strongly recommended.** A CPU-only path exists and needs no Docker, but it is slow enough that dictation stops feeling immediate — see [Performance](#performance) for measured numbers before you choose it.

> **Project status.** NexusVox is published as a reference implementation. It is complete and
> working, but not under active development — no new features are planned. Issues and pull
> requests are welcome; responses may be slow.

---

## Features

- **Push-to-talk** — hold `Ctrl+Shift+Alt`, speak, release; text is injected at the cursor instantly
- **GPU-backed transcription** — Voxtral (WebSocket streaming), Cohere and Parakeet run on NVIDIA GPUs via Docker; Whisper runs there too and is far quicker than on CPU
- **CPU fallback** — six Whisper variants run in-process with no Docker and no GPU, for machines that have no other option
- **Nine transcription models** — pick per recording in the dashboard
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

**Recommended — NVIDIA GPU** (Voxtral / Cohere / Parakeet, and a much faster Whisper):
- **NVIDIA GPU** with CUDA runtime installed (VRAM depends on model — see table below)
- **Docker Desktop** + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

**Fallback — CPU only** (Whisper in-process, no Docker):
- No additional dependencies — `pip install` and you're done.
- Expect a noticeable wait after every utterance. Read [Performance](#performance) first; on CPU you are choosing between accuracy and responsiveness, and cannot have both.

**Optional — [ffmpeg](https://ffmpeg.org/) on your `PATH`:** only needed to upload non-WAV audio files (MP3, FLAC, OGG, WEBM) in the dashboard's Upload tab. WAV uploads and push-to-talk dictation work without it. Install with `winget install Gyan.FFmpeg`, then verify with `ffmpeg -version`.

---

## Quick Start (GPU — recommended)

```bash
# 1. Start the Parakeet inference server (~2 GB VRAM)
cd docker && docker compose --profile parakeet up --build

# 2. Install
pip install -e .

# 3. Run
python -m nexusvox
```

Parakeet is the default because it is both the fastest and the most accurate model measured (see [Performance](#performance)) and needs only ~2 GB of VRAM. Voxtral offers WebSocket streaming but wants **≥16 GB** — switch to it in the dashboard Settings tab if your card has the headroom, and the server URL follows automatically.

## Quick Start (CPU — fallback)

```bash
pip install -e .
python -m nexusvox
```

The shipped default model is GPU-backed, so on a machine without a usable GPU the first run stops with a message telling you to pick a CPU-capable model. Open the dashboard at `http://localhost:47392` → Settings → **Model** and choose one of the Whisper entries — `whisper-small` for speed, `whisper-medium` for accuracy — then restart. Alternatively set `[inference].model = "whisper-small"` in `config.toml` before the first run.

Whisper downloads on first use (~150 MB for `whisper-small`, ~1.5 GB for `whisper-large-v3-turbo`) and caches under `%USERPROFILE%\.cache\huggingface`.

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

## Performance

Measured on the FLEURS test set with `scripts/benchmark.py`. **RTF** (real-time factor) is wall time divided by audio duration — RTF 0.05 means a 10-second utterance takes half a second to transcribe. **WER** is word error rate. Lower is better for both.

| Model | Device | Language | RTF | WER | 10 s of speech takes |
|---|---|---|---:|---:|---:|
| Parakeet TDT 0.6B | GPU | English | 0.017 | 5.8 % | 0.17 s |
| Parakeet TDT 0.6B | GPU | German | 0.012 | 5.2 % | 0.12 s |
| Whisper Large V3 Turbo | GPU | English | 0.055 | 5.6 % | 0.55 s |
| Whisper Large V3 Turbo | GPU | German | 0.048 | 5.8 % | 0.48 s |
| Whisper Medium | CPU | English | 0.681 | 5.6 % | **6.8 s** |
| Whisper Small | CPU | English | 0.235 | 7.5 % | 2.4 s |
| Whisper Small | CPU | German | 0.186 | 10.3 % | 1.9 s |

**Why a GPU is recommended.** On CPU you have to choose between accuracy and responsiveness, and you cannot have both:

- **Whisper Medium** reaches GPU-grade accuracy on CPU — 5.6 % WER, the same as Whisper Large V3 Turbo on GPU — but takes **6.8 seconds** for a 10-second utterance. That is 12× slower than the same accuracy on GPU, and 40× slower than Parakeet. Push-to-talk stops feeling like dictation and starts feeling like a batch job.
- **Whisper Small** is quick enough to be usable, but pays for it in errors: 7.5 % WER in English and **10.3 % in German** — twice Parakeet's German error rate. Word error rate is per word, not per sentence: at 10.3 %, a 15-word sentence averages more than one wrong word, so you are correcting almost every sentence by hand.
- On GPU there is no trade-off. Parakeet delivers 5.2 % WER in German *and* returns a 10-second utterance in about an eighth of a second.

A responsive push-to-talk experience needs roughly **RTF ≤ 0.20** (see [docs/BENCHMARK_GUIDE.md](docs/BENCHMARK_GUIDE.md)). Only the German Whisper Small run clears that bar on CPU, and it is the least accurate configuration measured.

**How to read these numbers.** They come from one machine — a 16 GB NVIDIA GPU for the GPU rows, that machine's CPU for the CPU rows — so treat the ratios as meaningful and the absolute values as indicative. The GPU rows cover the full FLEURS test split (647 English / 862 German samples); the CPU rows use 200 samples each, because a full CPU run takes hours. FLEURS is read speech, so real dictation with background noise and hesitation will score worse than any row here.

Reproduce with `python scripts/benchmark.py` — see [docs/BENCHMARK_GUIDE.md](docs/BENCHMARK_GUIDE.md).

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
- [SECURITY.md](SECURITY.md) — what NexusVox does on your machine, and how to report a vulnerability

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
