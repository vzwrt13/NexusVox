# Running Polish, Auto Cleanup and summaries with little or no GPU

Status: stage 1, desk research only. **No model was downloaded and nothing was measured.**
Every speed figure below is a published or third-party number, or an estimate marked as such.
Stage 2 (measurements) needs downloads; the list is at the end and waits for approval.

## Why this matters

Polish, Auto Cleanup and meeting summaries all go through the `[llm]` model. Today the
default is `gemma4:e4b` via Ollama, and the dashboard greys these features out without a
CUDA GPU. Dictation must stay fast, so the question is which model and runtime give an
acceptable result per hardware tier, and what NexusVox should recommend.

## What the workload looks like

| Feature | Input | Output | What dominates |
|---|---|---|---|
| Auto Cleanup | one dictation, ~10-100 words | about the same length | latency of a short prompt + short reply; blocks typing |
| Polish | selected text + spoken instruction | rewritten text | same, a little longer |
| Meeting summary | up to an hour of transcript, 5-15k tokens | a few hundred tokens | prompt processing (prefill); can run in the background |

On a CPU, token generation is limited by memory bandwidth and prefill by compute. A short
cleanup is mostly generation, so a small model matters more than a clever runtime. A long
summary is mostly prefill, so it takes minutes on a CPU. That is acceptable only because
it is not interactive.

## Runtimes

| Runtime | CPU-only | Notes for NexusVox | Verdict |
|---|---|---|---|
| **llama.cpp** (also behind Ollama, LM Studio) | yes, AVX2/AVX-512/ARM NEON, GGUF 4-8 bit | `protocol = "openai"` or `"ollama"` already works; one engine for CPU and GPU | **Keep as the single runtime** |
| **Ollama** | yes (llama.cpp inside) | Already the default. Vulkan is opt-in since 0.12.11 (`OLLAMA_VULKAN=1`), which can help AMD/Intel iGPUs; anecdotal reports show roughly 3x over CPU on a Vega laptop. iGPU memory limits in BIOS apply. | Keep; document Vulkan as an option |
| **bitnet.cpp** (Microsoft) | yes, ternary 1.58-bit kernels | Claims 2.4-6.2x speedup and 72-82% less energy on x86 against full precision. Only a handful of models (BitNet b1.58 2B4T, Falcon3/Falcon-E, a Llama3-8B conversion). No stated language support; likely English-centric. | Interesting, not a default: no German-capable model known |
| **OpenVINO GenAI** | yes, plus Intel iGPU/NPU | One third-party laptop test had llama.cpp CPU about 2x faster than OpenVINO CPU; NPU/iGPU gains exist but are Intel-only and the numbers are not rigorous. | Skip for now: a second runtime for one vendor |
| **ONNX Runtime GenAI** | yes, int4 | No benchmark found. NexusVox already ships `onnx-asr` for Parakeet, but an LLM path would mean a second server and model format. | Skip |
| **LiteRT-LM** (Google AI Edge) | yes | Packages Gemma 4 (E2B 2.58 GB, E4B 3.65 GB). Mobile-first, not an OpenAI-compatible server. | Not suited |

Takeaway: no engine beats llama.cpp enough on a typical Windows laptop to justify a second
code path. The `[llm]` section already speaks both Ollama and OpenAI protocols, so a
bundled or auto-managed llama.cpp server fits without protocol changes.

## Models

Sizes are for ~4-bit GGUF unless stated. "Speed" is a rule of thumb from third-party posts
(3B-class ≈ 15-30 tok/s, 7B-class ≈ 10-15 tok/s on modern laptop CPUs), not a measurement.

| Model | Params | Approx. 4-bit size | Licence | German | Comment |
|---|---|---|---|---|---|
| Gemma 4 E2B | ~2B effective | ~2.6 GB (LiteRT package) | Apache 2.0 | trained on 140+ languages, 35+ supported out of the box | **Best small candidate**: same family as today's default |
| Gemma 4 E4B | 4.5B effective (8B with embeddings) | ~3.7 GB (LiteRT package) | Apache 2.0 | MMMLU 76.6% | Current default. Fits a 6-8 GB GPU; slow but usable on a fast CPU |
| Qwen3 4B (Instruct 2507) | 4B | ~2.5 GB | Apache 2.0 (verify on card) | multilingual, stronger than Phi outside English per one roundup | Strong alternative for CPU |
| Qwen3 1.7B | 1.7B | ~1.1 GB | Apache 2.0 (verify on card) | weaker; risk of over-editing | Emergency fallback for very weak machines |
| Phi-4-mini | 3.8B | ~2.5 GB | MIT per one source, Apache 2.0 per another: **verify** | reported weaker outside English | English-first; not a default |
| Llama 3.2 3B | 3B | ~2 GB | Llama 3.2 Community Licence (attribution, usage terms) | official German support, modest quality | Licence is not plain permissive; avoid as default |
| BitNet b1.58 2B4T | 2.4B | ~0.4 GB | MIT repo; weights licence unchecked | no language claims | Only interesting if a multilingual 1-bit model appears |

Licence relevance: NexusVox is free for private use and sold commercially (Core and Pro).
Apache 2.0 and MIT allow that without extra terms. The Llama community licence adds
conditions and Gemma (before version 4) had its own terms; Gemma 4 is listed as Apache 2.0
on its Hugging Face cards, which removes that concern.

Quality: no source tested these models on German text cleanup or meeting summaries.
Aggregate benchmarks (MMMLU, MMLU-Pro) do not predict "fixes commas without rewriting".
The cleanup code already rejects implausible replies, which limits the damage of an
over-eager small model. A blind test on German and English dictations (stage 2) is the only
real answer.

## Recommended defaults per tier (to be confirmed in stage 2)

| Tier | Hardware | Default | Alternative | Features |
|---|---|---|---|---|
| **No GPU** | CPU only, 8-16 GB RAM | `gemma4:e2b` | Qwen3 4B Instruct; Qwen3 1.7B on 8 GB machines | Cleanup and Polish on, with a latency hint (estimate: a few seconds per dictation, to be measured). Summaries run as a background job |
| **Small GPU** | 4-8 GB VRAM, shared with the speech model | `gemma4:e4b` | `gemma4:e2b` if VRAM is tight | All features. Mind `keep_alive` so the speech model keeps its VRAM |
| **Large GPU** | 12+ GB VRAM | `gemma4:e4b` or a larger Gemma 4 | Qwen3 8B/14B | All features; a bigger model buys polish quality, not speed |

Note: the `config.example.toml` comment mentions `gemma4:12b`. The Hugging Face listing
shows the Gemma 4 family as E2B, E4B, 26B MoE and 31B dense; there is no 12B. Check the
Ollama tag names before documenting larger models.

## Config and UI changes that would make the choice easy

1. **Detect the tier at start** (CUDA present? VRAM? RAM? CPU threads) and preselect the
   model from the table above instead of the fixed `gemma4:e4b`.
2. **Replace the CUDA gate with a capability check.** Instead of greying out Cleanup,
   Polish and summaries, allow them on CPU with a visible hint ("about N s per dictation on
   this machine") once a short self-test has measured it. Grey out only if the measured
   latency is above a limit.
3. **Model picker with three presets** (Light / Balanced / Quality) in the dashboard, each
   mapped to a model, size on disk, RAM/VRAM and licence, so the user sees the disk cost
   before anything downloads.
4. **Explicit download step with size shown and confirmation.** Never pull a model silently.
5. **Summaries as a background job on CPU**, with a progress indicator and chunked input
   (map-reduce on 2-3k token blocks) so prefill cost stays bounded.
6. **Document Vulkan** (`OLLAMA_VULKAN=1`) as an opt-in for AMD/Intel iGPUs.

## Open questions for the maintainer

- Is "CPU Cleanup takes several seconds per dictation" acceptable, or should CPU users get
  Polish and summaries only, with Cleanup off?
- Should NexusVox manage the LLM runtime itself (bundled llama.cpp server) or keep telling
  users to install Ollama?
- Which hardware should the stage 2 measurements run on (this PC has an RTX 5070 Ti, so a
  CPU-only run means forcing `num_gpu=0`; a real laptop number would be better)?

## Stage 2: measurements that need downloads (waiting for approval)

Approve one by one. Sizes are approximate.

| Model | Size | Why |
|---|---|---|
| `gemma4:e2b` | ~2.6-3 GB | Proposed no-GPU default; measure CPU latency and German cleanup quality |
| `qwen3:4b` (Instruct 2507) | ~2.5 GB | Main alternative; compare German quality and over-editing |
| `qwen3:1.7b` | ~1.1 GB | Floor for weak machines |
| `gemma4:e4b` | already installed | Baseline; run with `num_gpu=0` for the CPU number |

Total new disk: about 6-7 GB if all three are approved. Skip BitNet, Phi-4-mini and Llama
until a reason appears.

## Sources

- [Gemma 4 E4B-it model card](https://huggingface.co/google/gemma-4-E4B-it) (Apache 2.0, 4.5B effective / 8B total, MMMLU 76.6%)
- [Gemma 4 on LiteRT-LM](https://developers.google.com/edge/litert-lm/models/gemma-4) (package sizes)
- [microsoft/BitNet](https://github.com/microsoft/BitNet) (speedup claims, supported models)
- [Ollama 0.12.11 Vulkan](https://www.phoronix.com/news/ollama-0.12.11-Vulkan)
- [Deploying LLMs on CPU-only environments with llama.cpp](https://ceur-ws.org/Vol-4164/paper11.pdf)
- [CPU-Only LLMs 2026](https://insiderllm.com/guides/cpu-only-llms-what-actually-works/)
- [Best small language models 2026](https://tinyweights.dev/posts/best-small-language-models-2026/)
- [Phi-4 Mini vs Gemma 3 vs Llama 3.2](https://tech-insider.org/phi-4-mini-vs-gemma-3-vs-llama-3-2-2026/)
- [Best small and edge LLMs 2026](https://aiportalx.com/blog/best-small-edge-llms-2026-qwen3-4b-gemma-phi-4)

Qwen and Phi licence entries come from aggregator pages, not the model cards; confirm them
before they appear in user-facing docs.
