# Parakeet on CPU only: test results

Measured 2026-10-31 for the 1.0 goal "Parakeet on CPU, without Docker, as the default".

**Verdict: supported as the default for dictation on a modern desktop CPU (8 cores / 16 threads or
similar), with two caveats.** Audio longer than ~20 s loses accuracy on the CPU path (German badly),
and a 4-thread machine is usable but slow. DE and EN are the supported languages; es/fr/pt are
experimental, dictation only (see the language notes in `docs/model-research.md`).

## Setup

| | |
|---|---|
| CPU | AMD Ryzen 7 5800X, 8 cores / 16 threads, 47.9 GB RAM, Windows 11 |
| CPU model on disk | `istupakov/parakeet-tdt-0.6b-v3-onnx`, int8: `encoder-model.int8.onnx` 652 MB + `decoder_joint-model.int8.onnx` 18 MB (Hugging Face cache, 640 MB on disk), plus Silero VAD 2.3 MB. Nothing was downloaded. |
| Code path | `LocalParakeetTranscriber._recognize` (onnx-asr 0.12, onnxruntime CPUExecutionProvider), the same call the app makes |
| GPU reference | the project's Docker container (`nexusvox-parakeet`, NeMo fp, RTX 5070 Ti), same clips, HTTP |
| Data | first 25 clips of the FLEURS `test` split per language (de, en, es, fr, pt), 234-379 s of audio each; ~95 MB downloaded from the FLEURS tarballs (streamed, stopped after 25 clips) |
| WER | corpus WER on the project's normaliser (lowercase, no punctuation); FLEURS references spell numbers as digits, Parakeet writes them out in German, which inflates DE WER on both paths |

Reproduce: `python scripts/parakeet_cpu_bench.py <fleurs-dir> --long-minutes 30`.

## Results

### Latency and cold start

| | CPU (16 threads) | GPU container |
|---|---|---|
| Model load (disk cache warm) | 2.4 s | 31 s container start |
| **First transcription** | **0.42-0.53 s** (10.6 s clip), same as the repeats | **17.0 s**, then 0.09-0.10 s |
| 3 s dictation | 0.15 s median | ~0.09 s |
| 8 s dictation | 0.33 s median | ~0.09 s |
| Whole FLEURS clip (10-20 s), median | 0.4-0.5 s | 0.09-0.10 s |

**The CPU path has no cold-start problem.** The 17 s first request on the GPU container does not
exist on CPU: the load is 2.4 s, and `connect()` already preloads it in the background, so the first
dictation is as fast as the next. (First-ever launch still downloads ~670 MB.)

### Real-time factor and RAM (16 threads)

| | RTF | |
|---|---|---|
| Files, per language | 0.042-0.046 | GPU: 0.007-0.010 |
| 30 min meeting (VAD path) | **0.051** (92 s for 30.1 min) | |
| RAM after model load | 856 MB | |
| RAM peak, 30 min file | 2.2 GB | grows with the length of the audio buffer |

### Accuracy (same 25 clips per language)

| Language | CPU WER | GPU WER |
|---|---|---|
| en | 6.9 % | 5.8 % |
| **de** | **14.6 %** | 5.1 % |
| es | 3.0 % | 2.4 % |
| fr | 8.9 % | 5.7 % |
| pt | 4.9 % | 4.7 % |

(For scale, the full-test-set GPU runs from April: de 5.2 %, en 5.8 %.)

The German gap is almost entirely one cause: **clips longer than `MAX_DIRECT_S` (20 s) go through
the Silero-VAD segmentation path.** Split by length, CPU German WER is 6.8 % for clips under 20 s
(21 clips) and 38.6 % for the four clips over 20 s. The long ones drift mid-sentence into English
or even Cyrillic ("your thermish verhalten is not so gleich mäßig"). French shows the same pattern
more mildly (8.3 % vs 11.5 %); English was unaffected (no clips over 20 s). Raising the direct limit
to 40 s as an experiment cut the WER on those long clips from 26 % to 17 %, but the 43-46 s clips
still drift, so the real fix is better segmentation (or per-segment language pinning), not just a
bigger limit. This hits long dictations and file uploads, not typical push-to-talk use.

### Minimum hardware

The same run pinned to fewer logical processors (affinity mask; the timings below are for the Ryzen 5800X cores, a slower CPU scales
proportionally):

| Threads | 3 s dictation | 8 s dictation | File RTF | 5 min meeting |
|---|---|---|---|---|
| 16 | 0.15 s | 0.33 s | 0.045 | RTF 0.05 |
| 8 | 0.27 s | 0.52 s | 0.065 | RTF 0.064 (20 s) |
| 4 (2 cores + SMT) | 1.06 s | 2.46 s | 0.32 | RTF 0.29 (90 s) |

WER is identical at every thread count. 8 threads is comfortable; 4 threads works but an 8 s
dictation waits 2.5 s, so treat 4 threads as the floor and 8 as the recommendation. RAM: plan for
4 GB free (0.9 GB model, ~2 GB with a long file).

## Can Parakeet run on CPU without Docker today?

**Yes.** `model = "parakeet-tdt-0.6b"` with `device = "cpu"` runs in-process
(`transcribers/local_parakeet.py`); `onnx-asr` and `onnxruntime` are core dependencies in
`pyproject.toml`, so no extra install is needed. On a machine without a GPU, `device = "auto"`
already resolves to this path. What is missing for "default for everyone":

1. **On a machine with an NVIDIA GPU, `auto` still picks the Docker container.** Making the CPU
   path the default there means changing the `auto` resolution (or the first-run default) in
   `resolve_device` / the config template.
2. **The >20 s accuracy problem above**, mainly for German.
3. **No speaker detection** on CPU (known, `SPEAKERS_NEED_GPU`).
4. **First launch downloads ~670 MB** from Hugging Face; the dashboard shows the state, but a
   packaged build could bundle or pre-fetch it.

## Recommendation

- **Supported as the default for dictation**: DE/EN WER matches the GPU path on clips under 20 s,
  latency is 0.15-0.5 s, there is no cold start, and it needs no GPU or Docker.
- **Minimum hardware:** 4 logical processors and 4 GB RAM (slow); recommended 8 threads / 8 GB.
- **Before it is the default for meetings and uploads,** fix the segmentation accuracy above 20 s.
  Until then GPU users with long recordings keep the GPU path.
