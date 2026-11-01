# Language models for Polish, Cleanup and summaries: CPU and GPU test results

Measured 2026-10-31 for the 1.0 goal "default `[llm]` model per hardware tier" (follow-up of
`docs/llm-low-gpu-research.md`). Three models through Ollama, each on the CPU only and on the GPU.

**Verdict.** `gemma4:e2b` is the default for machines without a usable GPU and for GPUs that cannot hold
both the speech model and a larger language model. `gemma4:e4b` stays the default where the speech model
and it fit together in VRAM, which is 12 GB and up with the Parakeet container (6.1 + 4.7 = 10.8 GB).
`qwen3.5:4b` is not recommended: it is slower on the CPU and it translates English text into German.
Auto Cleanup on the CPU gives a usable answer in 2 s (short dictation) to 6 s (about 100 words) with
`gemma4:e2b`, so it should stay off by default (as already decided) and show a latency hint.
**The app does not allow this today**: `cleanup.cleanup()` returns the text unchanged without a CUDA GPU
and the dashboard refuses to set a level (`NO_CUDA_HINT`, `dashboard/api.py`). Enabling Cleanup on CPU
needs a code change (relax the `needs_cuda` gate, show the hint); the numbers here were taken by calling
Ollama directly, not through the app.

## Setup

| | |
|---|---|
| CPU / RAM | AMD Ryzen 7 5800X, 8 cores / 16 threads, 47.9 GB, Windows 11 |
| GPU | RTX 5070 Ti, 16 GB. The Parakeet container was running idle and held 6.1 GB VRAM, as it does in daily use |
| Runtime | Ollama 0.32.4, default quantisation of each tag, `num_ctx` 4096 (16384 for the summary), `think: false` |
| CPU mode | `num_gpu = 0`: every layer on the CPU (`ollama ps` showed `100% CPU`). GPU mode showed `100% GPU` |
| Models | `gemma4:e2b` (4.6 GB on disk, the only download), `gemma4:e4b` (6.6 GB, current default), `qwen3.5:4b` (3.3 GB) |
| Prompts | the app's own: `cleanup.PROMPTS` (`light`, `medium`), `polish.SYSTEM_PROMPT`. NexusVox has no summary feature yet, so the summary prompt is the benchmark's own (decisions, action items with owner, open questions, at most 200 words) |
| Short dictation | 14 German words ("Ähm also ich wollte dir kurz sagen ..."); an English one of 12 words |
| Long dictation | 108 German words, with fillers |
| Meeting | 5,307 words of German, 8,485 prompt tokens (about 35 min of speech at 150 words/min). Not a real recording: `gemma4:12b` wrote six topic blocks of 4 speakers with fillers, numbers and decisions. It is the right size for the speed numbers; the summaries were read by eye, not scored |
| Repetitions | latency is the median of 3 requests on the GPU and 2 on the CPU, wall time of the HTTP call, model already loaded; the summary is a single run |

### Reproduce

```bash
ollama pull gemma4:e2b
pip install psutil          # only for the RAM figures
python scripts/llm_bench.py --model gemma4:e2b --mode gpu --meeting meeting_de.txt --out results/e2b-gpu.json
python scripts/llm_bench.py --model gemma4:e2b --mode cpu --meeting meeting_de.txt --skip-quality --reps 2 --out results/e2b-cpu.json
```

`--meeting` takes any transcript text file; if the file does not exist, the script has `--meeting-model`
(default `gemma4:12b`) write one first. `--mode cpu` needs no NVIDIA GPU (VRAM is then left out). The script talks to `127.0.0.1`: with `localhost` every
request on this Windows machine waited an extra 2 s (IPv6 tried first), which would have hidden the real numbers.

## Results

### Load time and memory

Cold load after `ollama stop`, files in the disk cache. The first load from a cold disk adds the read
time of the file.

| | Load, GPU | Load, CPU | VRAM (4k ctx / 16k ctx) | RAM, CPU mode | RAM, GPU mode |
|---|---|---|---|---|---|
| `gemma4:e2b` | 5.9 s | 5.4 s | 3.1 / 3.4 GB | 5.0 GB | 3.2 GB |
| `gemma4:e4b` | 9.1 s | 7.2 s | 4.7 / 5.1 GB | 7.2 GB | 3.6 GB |
| `qwen3.5:4b` | 4.6 s | 3.6 s | 3.9 / 4.4 GB | 3.7 GB | 1.1 GB |

VRAM is the change in `nvidia-smi` memory used (all GPUs); RAM is the change in system-wide memory used
(`psutil.virtual_memory().used`, all processes) from before the load to after the model answered a
cleanup request. It includes memory-mapped model pages that are resident, so it is what the machine loses,
not what one process reports. The first measurement run read the Ollama processes' working set instead,
which showed almost nothing for a memory-mapped model, so these figures come from a second pass with the
system-wide method that `scripts/llm_bench.py` now uses. The E-models keep a large embedding table in RAM even on the GPU. Add
about 0.4 GB for the 16k summary context.

### Auto Cleanup and Polish latency (median, model loaded)

| | `gemma4:e2b` CPU | `gemma4:e4b` CPU | `qwen3.5:4b` CPU | `gemma4:e2b` GPU | `gemma4:e4b` GPU | `qwen3.5:4b` GPU |
|---|---|---|---|---|---|---|
| Cleanup medium, short DE | 1.7 s | 3.3 s | 4.2 s | 0.52 s | 0.53 s | 0.40 s |
| Cleanup medium, short EN | 1.8 s | 2.8 s | 3.4 s | 0.45 s | 0.46 s | 0.39 s |
| Cleanup light, short DE | 1.9 s | 3.3 s | 3.9 s | 0.51 s | 0.51 s | 0.39 s |
| Cleanup medium, long DE | 5.9 s | 10.8 s | 21.1 s | 0.92 s | 1.13 s | 1.25 s |
| Cleanup light, long DE | 6.0 s | 11.5 s | 20.6 s | 0.91 s | 1.05 s | 1.27 s |
| Polish, short | 1.2 s | 2.3 s | 2.5 s | 0.49 s | 0.51 s | 0.37 s |
| Polish, long, "make it shorter" | 5.5 s | 7.4 s | 11.2 s | 0.87 s | 0.88 s | 0.81 s |
| Generation speed | 37-47 tok/s | 20-29 tok/s | 8-10 tok/s | 200-330 tok/s | 200-255 tok/s | 160-170 tok/s |

A cleanup prompt is about 340 tokens before the dictation, and the CPU reads it in well under a second;
the time is almost all generation, so it grows with the length of the reply. On the CPU the long
dictation is therefore the case that matters: Cleanup blocks typing, and its timeout is
`[cleanup].timeout_s`. On the GPU all three models are fast enough that the model choice does not
change the feel of dictation.

### Meeting summary (8,485 tokens in, 30-35 min of speech)

| | Total | Prefill | Generation | Reply |
|---|---|---|---|---|
| `gemma4:e2b` CPU | 82 s | 57 s (148 tok/s) | 20 s | 407 tokens |
| `gemma4:e4b` CPU | 145 s | 104 s (82 tok/s) | 35 s | 350 tokens |
| `qwen3.5:4b` CPU | 176 s | 90 s (94 tok/s) | 83 s | 480 tokens |
| `gemma4:e2b` GPU | 9.1 s (incl. 6.4 s load) | 0.9 s | 1.8 s | 436 tokens |
| `gemma4:e4b` GPU | 9.4 s (incl. 6.5 s load) | 1.3 s | 1.6 s | 261 tokens |
| `qwen3.5:4b` GPU | 7.8 s (incl. 3.8 s load) | 1.1 s | 2.9 s | 463 tokens |

A summary on the CPU takes 1.5-3 minutes: fine as a background job, not as something to wait for. The
context must be raised for it: Ollama's default of 4096 tokens would silently cut a meeting transcript.
All three summaries were usable German with decisions, owners and open questions; `gemma4:e4b` was the
tersest, `gemma4:e2b` listed the most detail per owner, `qwen3.5:4b` added a section per topic. No summary
was scored, so treat this as "no obvious failure", not a ranking.

### Quality against the current default

The 16 samples of `scripts/cleanup_eval.py` (German, English and mixed dictations, questions and orders
that must not be answered, digits, a snippet placeholder), at both levels, with the app's plausibility
guard. "Problems" counts a lost word or a filler that stayed (the checks of `cleanup_eval._check`). A reply the
guard rejects is listed apart: the app then types the dictation unchanged, so it is a safe miss, not a
wrong text.

| | `light` problems / rejected | `medium` problems / rejected | Notes |
|---|---|---|---|
| `gemma4:e4b` (default) | 0 / 0 | 0 / 1 | The one rejection merged two lines into one sentence |
| `gemma4:e2b` | 0 / 2 | 0 / 2 | Rejected: the two-line sample (lines merged or split) and the sample with the snippet placeholder (the model dropped it). No answered questions, no translation |
| `qwen3.5:4b` | 0 / 0 | 3 / 1 | At `medium` it wrote "Treffen" for "Meeting", "drei" for "3" and left a "hm" in; it also **translated an English dictation into German**, which the guard caught |

Out of 16 samples per level, `gemma4:e2b` falls back to the unchanged text twice where the default does not,
so Cleanup does less often on awkward input (multi-line dictations, snippets); it did not damage text.

Polish, four samples (two German, two English, with an instruction such as "Make it more formal" or
"Fasse das kürzer"): all three models handled the German selections and the instructions in both
languages. The English selection "We shipped the update yesterday ..." with the instruction "Improve
this text" came back in German from `qwen3.5:4b` in 3 of 3 repeats, with the app's default instruction as
well, and from `gemma4:e4b` in 3 of 3 repeats with the short instruction (with the default instruction it
stayed English). `gemma4:e2b` kept English every time. So `gemma4:e2b` is not worse than the current
default here, and `qwen3.5:4b` is clearly worse. Four selections are a small sample. The repeats and the
two-instruction comparison came from an ad-hoc run with the same prompts; `llm_bench.py` now records them
as `polish_language` (3 replies per instruction, German detected by common words), but that part of the script
has not been re-run since it was added, so the numbers above are not yet reproduced by it.

## Recommendation per hardware tier

VRAM arithmetic uses the measured figures: the Parakeet container holds 6.1 GB, `gemma4:e2b` 3.1 GB and
`gemma4:e4b` 4.7 GB (at 4k context; about 0.4 GB more for the summary).

| Tier | Default | Why | Features |
|---|---|---|---|
| **No GPU** (8+ cores, 16 GB RAM) | `gemma4:e2b` on the CPU | About 2x the CPU speed of `gemma4:e4b` for cleanup, 5 GB RAM instead of 7, keeps the language | Polish on. Cleanup selectable, off by default, with a hint of "about 2 s for a sentence, 6 s for a paragraph on this class of CPU" (needs the gate change above). Summaries as a background job (1.5 min per 35 min of speech) |
| **4-8 GB VRAM** | `gemma4:e2b`, placed by what the card holds | Container + `gemma4:e2b` is 9.2 GB, which does not fit. On a 6-8 GB card run **either** the speech model on the CPU (Parakeet CPU, 0.8 GB RAM) and `gemma4:e2b` on the GPU (3.1 GB, 0.5 s per cleanup), **or** keep the container and run `gemma4:e2b` on the CPU (this table's CPU column). `gemma4:e4b` (4.7 GB) fits on an 8 GB card only with the speech model on the CPU | All features, at GPU speed only in the first layout. The GPU latencies in this document were taken with 10 GB free, so they do not apply to a 6-8 GB card sharing VRAM with the container |
| **12+ GB VRAM** | `gemma4:e4b` | Container + `gemma4:e4b` is 10.8 GB, best cleanup result of the three, 0.5-1.1 s | All features. `gemma4:12b` (7.6 GB installed) is an optional step up; with the container it needs about 13.7 GB, so a 16 GB card. It was not measured here |

Slower CPUs than the 5800X (4 threads, laptops) will land well above these figures; the Parakeet CPU test
saw about 2x slower speech on 4 threads, and a language model is memory-bandwidth bound in the same way.
Not measured: Vulkan (`OLLAMA_VULKAN=1`), an Intel/AMD iGPU, a machine with 8 GB RAM in total (E2B needs 5 GB).

## Were the research and ticket current?

`docs/llm-low-gpu-research.md` was partly out of date:

- **Qwen3 4B is superseded.** Ollama's library now lists `qwen3.5` (0.8b, 2b, 4b, 9b, 27b, 35b, 122b; the
  4b tag is 3.3 GB) and its `qwen3` page names no newer generation. `qwen3:4b` was not downloaded; the
  installed `qwen3.5:4b` stands in for the "Qwen3 4B" candidate.
- **`gemma4:12b` exists** (7.6 GB installed, 7.7-8.0 GB on the library page, 256K context, in the Ollama library and installed here). The research
  note that the family has no 12B was wrong; it is corrected there. `config.example.toml` keeps the mention.
- Gemma 4 on Ollama is E2B, E4B, 12B, 26B and 31B, with no newer Gemma generation listed.
