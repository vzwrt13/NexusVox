# Parakeet on CPU only: test results

Measured 2026-10-31 for the 1.0 goal "Parakeet on CPU, without Docker, as the default".

**Verdict: supported as the default for dictation on a modern desktop CPU (8 cores / 16 threads or
similar), with two caveats.** Audio longer than ~20 s loses accuracy on the CPU path (German badly),
and a 4-thread machine is usable but slow. DE and EN are the supported languages; es/fr/pt are
experimental, dictation only (see `docs/model-research.md`).

## Setup

| | |
|---|---|
| CPU | AMD Ryzen 7 5800X, 8 cores / 16 threads, 47.9 GB RAM, Windows 11 |
| CPU model on disk | `istupakov/parakeet-tdt-0.6b-v3-onnx`, int8: `encoder-model.int8.onnx` 652 MB + `decoder_joint-model.int8.onnx` 18 MB (Hugging Face cache, 640 MB on disk), plus Silero VAD 2.3 MB. Nothing was downloaded. |
| Code path | `LocalParakeetTranscriber._recognize` (onnx-asr 0.12, onnxruntime CPUExecutionProvider), the call the app makes |
| GPU reference | the project's Docker container (`nexusvox-parakeet`, NeMo, RTX 5070 Ti), same clips, over HTTP |
| Data | first 25 clips of the FLEURS `test` split per language (de_de, en_us, es_419, fr_fr, pt_br), 234-379 s of audio each. `--fetch` streams the tarball and stops after 25 clips: ~95 MB transferred, not the 300-650 MB archives. |
| WER | corpus WER on the same normaliser as `scripts/benchmark_parakeet.py` (lowercase, no punctuation). FLEURS references spell numbers as digits and Parakeet writes them out in German, which inflates DE WER on both paths. Small samples (25 clips), so differences of a point or two are noise. |

### Reproduce

Windows only for the RAM figures; the rest also runs elsewhere. The clip folders are named after the
FLEURS codes above.

```bash
# CPU, all threads; downloads the clips on the first run (--fetch is a no-op once refs.tsv exists)
python scripts/parakeet_cpu_bench.py fleurs --fetch 25 --long-minutes 30
# minimum hardware: pin to the first N logical processors
python scripts/parakeet_cpu_bench.py fleurs --threads 4 --long-minutes 5
# the 40 s experiment below
python scripts/parakeet_cpu_bench.py fleurs --max-direct-s 40 --long-minutes 0
# GPU container (cd docker && docker compose --profile parakeet up), same clips
python scripts/parakeet_cpu_bench.py fleurs --gpu-url http://localhost:8002/v1/audio/transcriptions
```

## Results

### Latency and cold start

| | CPU, 16 threads | GPU container |
|---|---|---|
| Model load (disk cache warm) | 2.8 s (+0.07 s VAD) | ~31 s container start |
| First transcription (17.9 s clip) | 0.86 s, then 0.67-0.81 s | 17.0 s in the first run after a container start; 0.12 s in the later run below |
| 3 s dictation, median | 0.18 s | 0.08 s |
| 8 s dictation, median | 0.34 s | 0.08 s |
| Whole FLEURS clip (10-25 s), median per language | 0.47-0.61 s | 0.09-0.11 s |

**The CPU path has no cold-start problem**: the first request costs ~0.2 s more than the next.
`connect()` also preloads the model in the background. The GPU container's slow first request was
real in the first run after a start (17 s) but did not reproduce in the second one, so it is
intermittent. (First-ever launch on CPU still downloads ~670 MB.)

### Real-time factor and RAM (16 threads)

| | CPU | GPU |
|---|---|---|
| RTF, files (per language) | 0.044-0.053 | 0.007-0.010 |
| RTF, 30 min meeting (VAD path) | **0.055** (100 s) | not measured |
| RAM, model loaded, no audio | **777 MB** | |
| RAM peak, 30 min file | 2.25 GB, of which ~0.2 GB is the benchmark's own buffers (93 MB of clips, 110 MB long audio) | |

The model itself needs ~0.8 GB; transcribing a 30-minute file grows that to ~2 GB, so long files
need real headroom.

### Accuracy (same 25 clips per language, all lengths)

| Language | CPU WER | GPU WER | CPU, clips ≤ 20 s | GPU, clips ≤ 20 s |
|---|---|---|---|---|
| de | **14.6 %** | 5.1 % | 6.8 % (21 clips) | 4.9 % |
| en | 6.9 % | 5.8 % | 6.9 % (25) | 5.8 % |
| es | 3.0 % | 2.4 % | 2.6 % (24) | 2.4 % |
| fr | 8.9 % | 5.7 % | 8.3 % (23) | 6.8 % |
| pt | 4.9 % | 4.7 % | 4.3 % (22) | 3.9 % |

On clips up to 20 s the CPU path is **0.2-1.9 points of WER behind the GPU** (German is the largest
gap, English 1.1). It is close, not identical; int8 quantisation is the likely reason, which was
not tested (no fp32 CPU model on disk).

The large German gap comes from **clips longer than `MAX_DIRECT_S` (20 s), which go through
Silero-VAD segmentation**: CPU German WER on the four clips over 20 s is 38.6 % (GPU 5.7 %). The
output drifts mid-sentence into English or even Cyrillic ("your thermish verhalten is not so
gleich mäßig"). French (11.5 %, two clips), Portuguese (7.3 %, three) and Spanish (7.8 %, one) are
hit less; English has no clip over 20 s. This affects long dictations and file uploads, not
typical push-to-talk use.

Raising the direct-decode limit to 40 s (`--max-direct-s 40`) is **not a fix**: WER on the
over-20 s clips goes de 38.6 → 30.0 %, es 7.8 → 3.9 %, fr 11.5 → 2.5 %, but pt 7.3 → 11.3 %, and the
two German clips over 40 s still drift. The cause needs a proper look (segment boundaries, language
carry-over between segments) in its own ticket.

### Minimum hardware

Pinned with `--threads N` (first N logical processors of the Ryzen 5800X; a slower CPU will be
proportionally slower). WER is identical at every thread count.

| Threads | 3 s dictation | 8 s dictation | RTF, files | RTF, 5 min meeting |
|---|---|---|---|---|
| 16 | 0.18 s | 0.34 s | 0.044-0.053 | 0.055 (30 min) |
| 8 | 0.26 s | 0.51 s | 0.061-0.068 | 0.067 |
| 4 (2 cores + SMT) | 1.03 s | 2.50 s | 0.32-0.35 | 0.296 |

8 threads is comfortable. 4 threads works, but an 8 s dictation waits 2.5 s and a file runs at
about a third of real time, so treat 4 as the floor. RAM: plan for 4 GB free (0.8 GB model, ~2 GB
with a 30-minute file).

## Can Parakeet run on CPU without Docker today?

**Yes.** `model = "parakeet-tdt-0.6b"` with `device = "cpu"` runs in-process
(`transcribers/local_parakeet.py`); `onnx-asr` and `onnxruntime` are core dependencies in
`pyproject.toml`, so no extra install is needed. On a machine without a GPU, `device = "auto"`
already resolves to this path. What is missing for "default for everyone":

1. **On a machine with an NVIDIA GPU, `auto` still picks the Docker container.** Making the CPU
   path the default there means changing the `auto` resolution (or the first-run default) in
   `resolve_device` and the config template.
2. **The >20 s accuracy problem above**, mainly for German.
3. **No speaker detection** on CPU (known, `SPEAKERS_NEED_GPU`).
4. **First launch downloads ~670 MB** from Hugging Face; the dashboard shows the state, but a
   packaged build could bundle or pre-fetch it.

## Recommendation

- **Supported as the default for dictation**: clips up to 20 s are within ~1-2 WER points of the
  GPU path (German +1.9, English +1.1), latency is 0.2-0.6 s, there is no cold start, and it needs
  no GPU or Docker.
- **Minimum hardware:** 4 logical processors and 4 GB RAM (slow); recommended 8 threads / 8 GB.
- **Before it is the default for meetings and uploads,** fix the segmentation accuracy above 20 s.
  Until then, users with a GPU and long recordings keep the GPU path.
