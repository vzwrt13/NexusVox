# NexusVox Benchmark Guide

A practical guide to measuring transcription quality (WER) and speed (RTF) of NexusVox's models on the FLEURS dataset.

## What the benchmark measures

`scripts/benchmark.py` runs each model on Google's FLEURS test set and records two metrics per sample:

- **WER (Word Error Rate)** — fraction of words that differ from the reference transcript after normalization (lowercase, no punctuation). Lower is better. We target **< 8 % EN** and **< 12 % DE**.
- **RTF (Real-Time Factor)** — wall time divided by audio duration. RTF = 0.2 means 10 s of audio took 2 s to transcribe. Lower is better. We target **RTF ≤ 0.20 on CPU** for a responsive push-to-talk UX.

Results are written to `benchmarks/<model_key>_<lang>_<timestamp>.json` and automatically surfaced in the Dev tab of the dashboard (append `?dev` to the dashboard URL).

## Prerequisites

One-time setup:

```bash
pip install jiwer requests tqdm soundfile librosa numpy
pip install "datasets<3.0.0"
```

For CPU (in-process) benchmarks you also need `faster-whisper`, which is already in the project's `pyproject.toml` dev extras.

FLEURS samples stream from HuggingFace Hub — no manual download, but the first run of each language pulls ~several hundred MB of audio. Set `HF_HUB_OFFLINE=0` (default) to allow downloads.

## Two benchmark modes

### 1. Server-backed (GPU)

For models that run inside a Docker container — `voxtral-mini-4b`, `cohere-transcribe`, `parakeet-tdt-0.6b`, and the GPU Whisper build. The benchmark POSTs WAVs to the HTTP endpoint.

```bash
# Start the container first (requires NVIDIA GPU)
cd docker && docker compose --profile whisper up --build

# Then, in another terminal:
python scripts/benchmark.py --model whisper-large-v3-turbo --lang en_us --limit 200
```

The script runs a `/health` probe before starting and aborts with an actionable message if the container isn't up.

### 2. In-process (CPU)

For CPU-friendly models that run directly via `faster-whisper` — no Docker, no server. Add `--device cpu`:

```bash
python scripts/benchmark.py --model distil-whisper-large-v3 --lang en_us --limit 200 --device cpu
```

First run downloads the model (~200 MB–1.5 GB depending on size) to `~/.cache/huggingface/`. Subsequent runs reuse the cache.

## Common invocations

List every model the script knows about, grouped by mode:

```bash
python scripts/benchmark.py --list-models
```

50-sample smoke test:

```bash
python scripts/benchmark.py --model whisper-small --lang en_us --limit 50 --device cpu
```

Full CPU sweep for the current plan (both languages, 200 samples each):

```bash
python scripts/benchmark.py --model distil-whisper-large-v3  --lang en_us --limit 200 --device cpu
python scripts/benchmark.py --model distil-whisper-medium-en --lang en_us --limit 200 --device cpu
python scripts/benchmark.py --model whisper-medium           --lang both  --limit 200 --device cpu
python scripts/benchmark.py --model whisper-base             --lang both  --limit 200 --device cpu
```

CPU baselines for comparison:

```bash
python scripts/benchmark.py --model whisper-large-v3-turbo --lang both --limit 100 --device cpu
python scripts/benchmark.py --model whisper-small          --lang both --limit 100 --device cpu
```

## CLI reference

| Flag | Default | Purpose |
|------|---------|---------|
| `--model` | — (required) | Registry key, e.g. `whisper-medium` |
| `--lang` | `en_us` | `en_us`, `de_de`, or `both` |
| `--limit` | none (full split) | Max samples per language |
| `--device` | `gpu` | `gpu` (HTTP) or `cpu` (in-process) |
| `--compute-type` | `int8` on CPU, `float16` on GPU | CT2 quantization: `int8`, `int8_float16`, `float16`, `float32` |
| `--url` | model's default | Override the HTTP endpoint (GPU mode only) |
| `--split` | `test` | HuggingFace dataset split |
| `--timeout` | `120.0` | Per-sample timeout in seconds |
| `--list-models` | — | Print registry and exit |

## Reading the output

Each run prints a one-line summary and the saved file path:

```
en_us: WER=0.0558  RTF=0.051  samples=200  failed=0
Results: benchmarks/whisper-large-v3-turbo_en_us_20260406_062205.json
```

The JSON file contains a `summary` block (model, WER statistics, RTF, timestamp, device, compute_type) and a `samples` array with per-sample reference, hypothesis, WER, audio duration, and latency. The Dev tab consumes this structure directly — no extra post-processing needed.

Summary fields worth knowing:

- `wer_mean`, `wer_median`, `wer_p90` — central tendency and tail of per-sample WER.
- `rtf` — overall wall-time-to-audio ratio, including dataset streaming overhead.
- `failed_samples` — count of requests that errored out (timeout, connection error, etc.). A handful is tolerable; many means the run is unreliable.

## Interpreting results

A model is "CPU-shippable" for NexusVox if it hits, on your target CPU:

- **RTF ≤ 0.20** — 10 s of speech transcribes in ≤ 2 s, preserving the push-to-talk feel.
- **WER ≤ 8 % EN and ≤ 12 % DE** — comparable to the GPU baseline on FLEURS.

If one model clears both bars, it becomes the single default. If EN and DE have different winners, the registry can expose per-language defaults and the tray language toggle swaps automatically.

## Viewing results in the dashboard

Open the dashboard (default `http://localhost:47392`), append `?dev` to the URL, and switch to the **Dev** tab. All benchmark JSON files under `benchmarks/` are rendered as grouped charts — WER per model per language and RTF per model — so you can compare candidates side by side without opening the raw files.

## Troubleshooting

- **"Cannot reach … server at http://…:800X/health"** — the Docker container for that model profile isn't running. Start it with `docker compose --profile <profile> up`.
- **"Model is still loading"** — container came up but vLLM hasn't finished loading weights. Wait 20–60 s and retry.
- **"faster-whisper is required for --device cpu"** — install the dev extras: `pip install -e ".[dev]"`.
- **Very slow first CPU run** — model download (several hundred MB). Check `~/.cache/huggingface/` is growing. Subsequent runs reuse the cache.
- **`ModuleNotFoundError: datasets`** — install with `pip install "datasets<3.0.0"`. Newer versions break the FLEURS `trust_remote_code` flag.
- **RAM blowup running many CPU models back-to-back** — `LocalWhisperTranscriber._model_cache` retains every loaded model. Run one model per Python process when sweeping candidates.
