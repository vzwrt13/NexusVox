# Speech model research for 1.0

Status: research only, 2026-10-30 (issue #138). Nothing here changes the model list in code.

Method: **no model was downloaded and nothing was benchmarked locally.** Every number below is
published (model cards, papers, project READMEs) and says where it comes from. Numbers from different
cards use different normalisation, decoding and hardware, so compare them as orders of magnitude, not
as a ranking to the decimal. Anything not retrieved is marked "n/r". The measurements still worth doing
are listed under [Downloads that need approval](#downloads-that-need-approval).

## Recommendation

| Slot | Model | Why |
|---|---|---|
| **CPU default** | `parakeet-tdt-0.6b` (v3, in-process ONNX int8 through `onnx-asr`) | Already shipped. 25 languages with auto-detection, no Docker, no GPU, CC-BY-4.0. About 36x real time on a desktop CPU (9800X3D, onnx-asr README); FLEURS WER 5.04 German / 4.85 English. |
| **GPU default** | the same `parakeet-tdt-0.6b` v3 (Docker NeMo) | One model for both devices means one set of language behaviour and one licence. Cohere Transcribe has a lower Open ASR average (5.42 vs 6.34) but is 2B, gated and Docker-only. |
| Keep as extra choices | `whisper-large-v3-turbo`, `whisper-small`, `custom-server` | Turbo covers the 99-language case (Parakeet stops at 25); Small is the fallback for weak CPUs; the custom slot is the escape hatch for everything else. |
| Candidates to remove | `whisper-base`, `whisper-medium`, `distil-whisper-large-v3`, `distil-whisper-medium-en` | Four more Whisper variants a normal user cannot tell apart. The two distil models are English-only, so they break German. See [Curated list](#curated-list). |
| Decision for the maintainer | `voxtral-mini-4b`, `cohere-transcribe` | Docker, 6 to 16+ GB VRAM, and no accuracy edge that Parakeet v3 does not mostly cover. Recommend hiding them from the default list and pointing GPU tinkerers to `custom-server`. |
| Do not ship | Parakeet Ultra, Parakeet Redux | Proprietary Photon runtime, see below. |

## Survey

CPU speed is RTFx (seconds of audio per second of compute, higher is faster). GPU RTFx on the Open ASR
leaderboard is batched on a data-centre GPU and says nothing about single-clip dictation latency; it
only ranks models against each other.

| Model | Size | Languages | Accuracy (published) | Speed (published) | Runtime for NexusVox | Licence |
|---|---|---|---|---|---|---|
| **Parakeet TDT 0.6B v3** | 0.6B (~670 MB int8 ONNX) | 25 European | FLEURS: de 5.04, en 4.85, es 3.45, fr 5.15, pt 4.76, it 3.00. Open ASR avg 6.34 | GPU RTFx 3333; CPU about 36 (9800X3D) | `onnx-asr` ONNX int8, Windows, no Docker, no PyTorch (shipped) | CC-BY-4.0 |
| Parakeet TDT 0.6B v2 | 0.6B | English only | Open ASR avg 6.05 | GPU RTFx ~3380 | `onnx-asr` (same CPU figure family) | CC-BY-4.0 |
| Canary 1B v2 | ~1B | 25 European | Open ASR avg 7.15; per-language FLEURS n/r | GPU RTFx 749; CPU about 8 (onnx-asr) | `onnx-asr` | CC-BY-4.0 |
| Parakeet Ultra (`moondream/parakeet-ultra`) | 0.6B, F32/F16 | 25 | FLEURS de 3.61, en 3.55 (the same card quotes 4.13 / 4.25 for the original, which differs from NVIDIA's 5.04 / 4.85, so its normalisation is different) | B200 GPU, batch 128: ~9700x vs ~6000x for NeMo | Moondream **Photon** runtime (`pip install moondream`); "intended for GPUs" | Weights CC-BY-4.0; Photon licence not stated on PyPI |
| Parakeet Redux (`moondream/parakeet-redux`) | 178 MB, 1.58-bit ternary | 25 | FLEURS de 5.42 vs 4.13, en 4.90 vs 4.25; Open ASR en avg 6.55 vs 6.26; noisy German at 5 dB 12.36 vs 8.07 | 113x on 8 EPYC cores, 38x on an M2 | Photon only. Tested here 2026-10-16: no AVX2 kernels in the Windows wheel, fails with `NotImplementedError` | Weights CC-BY-4.0; Photon proprietary |
| Whisper large-v3-turbo | 809M | 99 | Whisper paper only publishes a chart; Qwen3-ASR report puts large-v3 at 5.27 macro-average on a 12-language FLEURS subset | README: ~8x large-v3 on A100 | `faster-whisper` in-process (shipped) | MIT |
| Whisper small / base / medium | 244M / 74M / 769M | 99 | n/r (no text table); small and base are clearly weaker in German | README relative speed ~4x / ~7x / ~2x | `faster-whisper` (shipped) | MIT |
| Distil-Whisper large-v3 / medium.en | 756M / 394M | English only | n/r | ~6x / ~4x faster than parent | `faster-whisper` (shipped) | MIT |
| Cohere Transcribe 03-2026 | 2B (BF16) | 14: EN FR DE IT ES PT EL NL PL ZH JA KO VI AR | Open ASR avg 5.42 (best in its own table); FLEURS per language only in a chart | GPU RTFx 525 | Docker (shipped); no CPU path | Apache-2.0, gated on HF |
| Voxtral Mini 4B Realtime | ~4B | 13 | FLEURS (480 ms delay): de 6.19, en 4.90, es 3.31, fr 6.42, pt 5.03, it 3.27 | streaming, <500 ms | vLLM, one GPU with at least 16 GB | Apache-2.0 |
| Qwen3-ASR 0.6B / 1.7B | 0.6B / 1.7B | 30 languages plus 22 Chinese dialects | FLEURS 0.6B: de 6.48 en 4.39 es 4.94 fr 7.72 pt 6.21 it 4.99. 1.7B: de 3.92 en 3.35 es 3.36 fr 4.75 pt 3.92 it 2.41 (technical report, table A.2b) | n/r | Transformers/vLLM; no mature Windows CPU runtime found | Apache-2.0 (one third-party guide says the HF download is gated; unverified) |
| IBM Granite 4.x speech (1B and 2B) | 1B / 2B | EN FR DE ES PT (+JA in 4.1) | mean WER 5.33 for the 2B on 8 datasets (third-party aggregator); per-language n/r | n/r | Transformers/vLLM; no CPU runtime found | Apache-2.0 |
| Moonshine | 27M / 61M (English); other languages are single-language "flavors" | English; AR ZH JA KO UK VI | English only is relevant, and weaker than Parakeet | n/r | n/r | MIT |
| GigaAM v3 | n/r | Russian | not relevant | CPU ~59 | `onnx-asr` | n/r |

What I could not find in public sources: a German/English comparison of Whisper small/medium/base next
to Parakeet in one table, CPU numbers for Granite and Qwen3-ASR, and any CPU number for Parakeet on the
maintainer's own machine (the 36x figure is a 9800X3D). Where this matters it is in the download list.

### Findings that drive the recommendation

- **Parakeet v3 is the only candidate that is small, multilingual, permissively licensed and already runs
  Docker-free on Windows CPU.** Every challenger is either bigger and GPU-first (Cohere, Voxtral, Qwen3-ASR
  1.7B, Granite), English-only (Parakeet v2, Moonshine, distil-Whisper), slower on CPU (Canary 1B v2, about
  8x vs 36x) or tied to a runtime that does not work here (Photon).
- **Parakeet Ultra** has a real accuracy edge on the card (German 3.61 vs 4.13), but it only runs inside
  Moondream's Photon runtime, whose licence is not stated on its package page, and the runtime is
  documented as GPU-oriented. Shipping it would make a proprietary binary a hard dependency of an AGPL
  product. Untested on the GPU here. Revisit if Moondream publishes the runtime licence and ONNX or NeMo
  weights load in a standard runtime.
- **Parakeet Redux** is tempting for weak CPUs (178 MB, 113x on server cores) but costs about a point of
  German WER, much more in noise, and it does not run on Windows x86 today (scalar-only kernels, tested
  2026-10-16). Not an option for 1.0.
- **Qwen3-ASR 1.7B and Granite 4.x speech** are the two to watch: Apache-2.0, better German than Parakeet
  on paper (Qwen3 1.7B: de 3.92). Neither has a CPU runtime NexusVox could use without PyTorch and a large
  download. Revisit after 1.0 when an ONNX or GGUF build exists.
- **CC-BY-4.0 needs attribution**, not a fee. Credit NVIDIA wherever the model is offered (README already has this).

## Curated list

Today the registry has 10 entries (`MODEL_REGISTRY`, `src/nexusvox/config.py`). Proposed:

| Entry | Verdict | Reason |
|---|---|---|
| `parakeet-tdt-0.6b` | **Keep, default (CPU and GPU)** | see above |
| `whisper-large-v3-turbo` | Keep | 99 languages, the answer for anything Parakeet does not cover; works on CPU but is heavy there |
| `whisper-small` | Keep | The low-end CPU fallback and a second multilingual choice |
| `custom-server` | Keep | Escape hatch; also the right home for Cohere, Voxtral and any future model |
| `whisper-base` | Remove | Too weak for German dictation; Small is the floor |
| `whisper-medium` | Remove | Sits between Small and Turbo with no clear reason to pick it |
| `distil-whisper-large-v3`, `distil-whisper-medium-en` | Remove | English only; a German-speaking user who picks one gets garbage. If an English-only fast model is wanted, Parakeet v2 through `onnx-asr` is the better one |
| `cohere-transcribe`, `voxtral-mini-4b` | Maintainer decision (recommend: hide) | Docker plus 6 to 16+ GB VRAM for a narrow gain; keeps the choice list short |

Result: three real choices (Parakeet, Whisper Turbo, Whisper Small) plus custom, instead of ten.
Removing a registry entry affects users whose `config.toml` names it: `app.py` already has a fallback
path for unknown models (`app.py:92-103`), so a removal PR should check it covers this case and note it
in the release notes.

## Licences

NexusVox is AGPL-3.0-or-later with a commercial licence on request, and ships **no weights**: models are
downloaded by the user from Hugging Face. The question "may we download or offer it in a product with
free private use and paid commercial use" reduces to: does the model licence allow commercial use and
redistribution/download by the user, and what does it demand in return. Not legal advice.

| Model | Licence | Commercial use | What it demands | Verified |
|---|---|---|---|---|
| Parakeet TDT 0.6B v3 / v2, Canary 1B v2 | CC-BY-4.0 | Yes ("ready for commercial/non-commercial use" on the card) | Credit NVIDIA, state changes (int8 ONNX conversion counts as a change; the converter's repo should be credited too) | model cards, 2026-10-30 |
| Parakeet Ultra / Redux weights | CC-BY-4.0 | Yes for the weights | same as above; **the Photon runtime has no stated licence** | model cards; PyPI page names none |
| Whisper family, faster-whisper conversions, Distil-Whisper | MIT | Yes | Keep the copyright notice | Whisper README; distil/Systran per README table, not re-fetched |
| Cohere Transcribe | Apache-2.0, gated | Yes | Accept the terms on HF; user needs an `HF_TOKEN` | model card |
| Voxtral Mini 4B Realtime | Apache-2.0 | Yes | notice | model card |
| Qwen3-ASR | Apache-2.0 | Yes | notice | technical report; gating claim unverified |
| Granite 4.x speech | Apache-2.0 | Yes | notice | HF blog, Codesota |
| `onnx-asr` (runtime) | n/r in this research (MIT per its repo, recheck) | n/r | | not verified |

All kept models (Parakeet, Whisper) allow commercial use, so Core and Pro can both offer them. Because the
user downloads weights themselves, NexusVox is not redistributing them; the attribution duty is still
cheap to honour and already in the README.

### Language models for Polish, cleanup and summaries (#93)

NexusVox does not ship an LLM either: `[llm]` points at a server the user runs (Ollama by default,
`gemma4:e4b`). The licence matters for the model the docs *recommend*.

| Model | Licence | Commercial use | Notes | Verified |
|---|---|---|---|---|
| Gemma 4 (`gemma4:e4b`, current default) | Apache-2.0 | Yes | 4.5B effective / 8B with embeddings, 140+ languages pre-trained, 35+ supported out of the box. The older Gemma Terms of Use no longer apply to Gemma 4 | official HF card, 2026-10-30 |
| TranslateGemma (translator server) | Gemma Terms of Use presumed (built on Gemma 3) | Yes under those terms, with a use policy | Check before documenting; it is a separate personal-tooling tool, not part of the public repo | **not verified** |
| Qwen3 text models | Apache-2.0 (my recollection) | Yes | candidate small LLMs for CPU-only polish | **not verified this session** |
| Llama 3.x | Llama Community Licence | Yes under conditions (attribution, user-count cap, use policy) | avoid as a recommended default | from memory, not verified |
| Phi-4-mini | MIT (my recollection) | Yes | small, CPU-friendly candidate | **not verified this session** |

The unverified rows should be checked against the model cards when #93 picks its CPU model.

## Language support beyond German and English

### What each model officially supports

Parakeet v3: bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk (25). Whisper:
99. Cohere: 14. Voxtral: 13. Qwen3-ASR: 30 plus dialects. Granite 4.x: 5 to 6. Full lists are in the survey
table; the three languages asked for are covered by every model above except the English-only ones.

### Published accuracy next to German and English

Parakeet TDT 0.6B v3 (the recommended default), FLEURS, greedy, no punctuation, NVIDIA card:

| de | en | es | fr | pt | it |
|---|---|---|---|---|---|
| 5.04 | 4.85 | 3.45 | 5.15 | 4.76 | 3.00 |

The card notes Portuguese is trained on European Portuguese while most benchmarks are Brazilian, so
expect the Brazilian figure to be somewhat worse in practice. These are the **published fp32 NeMo**
numbers. The requested own measurement of the int8 ONNX path on public audio was **not run** (it needs
downloads, see below); for dictation the expected gap is small, but that is an assumption, not a result.

For comparison (same benchmark, different papers): Voxtral at 480 ms: de 6.19 en 4.90 es 3.31 fr 6.42
pt 5.03 it 3.27. Qwen3-ASR 1.7B: de 3.92 en 3.35 es 3.36 fr 4.75 pt 3.92 it 2.41.

### What is language-bound in NexusVox today

Read from the code on this branch.

| Step | Where | Bound to | What breaks in, say, Spanish |
|---|---|---|---|
| Language setting and tray toggle | `SUPPORTED_LANGUAGES = ("en", "de")`, `config.py:478`; tray toggle in `app.py:988` flips en/de | en, de | A user cannot select any other language. Whisper gets `language=` from this setting unless auto-detect is on, so Whisper in Spanish needs auto-detect. Parakeet is called without a language and auto-detects, so it works regardless |
| Language detection | `lang_detect.py`: lingua built from English and German only; used for the post-transcription language, analytics, and `edit_watch.py` confidence | en, de | Spanish text is labelled en or de (whichever is closer) or "unknown". Wrong language tags in stats; learn-from-edits may misjudge a correction |
| Voice commands | `voice_commands.py`: structural and symbol lists, number words, `press_enter`; "Every vocabulary covers English and German" | en, de | "nueva línea" is typed as words; "veinticinco" is not converted to 25. Nothing crashes |
| nexus OS commands | `os_commands.py`: `open`/`öffne` verbs, the `nexus` trigger | en, de | "abre Chrome" is typed instead of opening it |
| Assistant forward and os-control | `assistant.py` trigger phrase "nexus assistant" | en, de wording | Trigger works if the word "nexus" is recognised; command text is passed through as spoken |
| Auto cleanup prompt | `cleanup.py`: filler list "um, uh, er, äh, ähm, hm"; "German stays German, English stays English" | en, de | Spanish fillers ("eh", "este", "o sea") are not named, so the model removes fewer. The language-keeping rule is worded for two languages; a capable model generalises, a small one may translate |
| Polish prompt | `polish.py`: "keep the meaning, the language and the author's tone", "keep the text's language" | language-neutral | Works as written; quality depends on the LLM's own language skill. Spoken preset names are the user's own words, so they work |
| Translator (translate before inject) | `translator.py`, local TranslateGemma server | source de/en/mixed, target English | `source` field only models de/en/mixed; other source languages should still translate if the server supports them, but this was not tested and the field mislabels them |
| Dictionary, snippets, learn-from-edits | `dictionary.py`, `snippets.py`, `edit_watch.py` | language-agnostic, except the lingua gate in `edit_watch.py` | Dictionary and snippets work in any language. Learn-from-edits uses lingua confidence (en/de) to decide which side of a correction is in a clear language |
| UI strings, docs | dashboard, README | English | Not a speech problem |

The bilingual-speech rule in `CLAUDE.md` is deliberately two languages. Adding a third means: a
language entry in the voice-command, number-word and OS-command vocabularies *with tests*, a lingua
language added to `lang_detect.py` and `edit_watch.py`, the cleanup filler list extended, and the
language selector in the tray and dashboard opened beyond en/de.

### What 1.0 can advertise

| Level | Languages | Basis |
|---|---|---|
| **Supported** | German, English | Full pipeline: transcription, voice commands, OS commands, cleanup, detection and tests in both |
| **Experimental** | Spanish, French, Portuguese, Italian | Parakeet v3 transcribes them well on paper (WER 3.0 to 5.2), so dictation works. Commands, number words, detection and cleanup fillers are not localised; no NexusVox tests. Say "dictation only". Pending the own measurement |
| **Transcription only, untested** | the other 19 Parakeet languages, and the 99 Whisper languages | Model-level claim, nothing verified in NexusVox |

Do not advertise a language as supported until its voice-command vocabulary and tests exist; that is the
line the bilingual rule already draws.

## Downloads that need approval

None done. The only items that would improve this document:

| What | Size | Why | Needed? |
|---|---|---|---|
| `nemo-parakeet-tdt-0.6b-v3` int8 ONNX (the shipped CPU model) | ~670 MB, probably already cached on the maintainer's PC | Measure real single-clip latency and WER of the int8 path on this machine (maintainer's Ryzen 7 5800X, not the 9800X3D in the published figure) | Recommended |
| FLEURS test subsets for de, en, es, fr, pt (a few hundred clips each, public, CC-BY-4.0) | est. 0.1 to 0.3 GB per language; exact size unknown until listed | Own WER of the int8 path next to the published fp32 figures, which is what the ticket asks for | Recommended |
| `whisper-small` (faster-whisper) | ~0.5 GB | Same test, fallback model | Optional |
| Parakeet Ultra on GPU | ~1.2 GB weights + Photon (~470 MB kernels, venv ~1.2 GB) | The only way to learn whether Ultra's German edge holds and runs on the RTX 5070 Ti; still ships a proprietary runtime | Optional; recommend skip until the Photon licence is clear |

Total for the two recommended items: under 2 GB.

## Sources

- [moondream/parakeet-ultra](https://huggingface.co/moondream/parakeet-ultra), [moondream/parakeet-redux](https://huggingface.co/moondream/parakeet-redux), [moondream on PyPI](https://pypi.org/project/moondream/)
- [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2), [nvidia/canary-1b-v2](https://huggingface.co/nvidia/canary-1b-v2)
- [CohereLabs/cohere-transcribe-03-2026](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026), [mistralai/Voxtral-Mini-4B-Realtime-2602](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)
- [openai/whisper](https://github.com/openai/whisper), [istupakov/onnx-asr](https://github.com/istupakov/onnx-asr)
- [Qwen3-ASR technical report](https://arxiv.org/html/2601.21337), [Granite 4.0 1B Speech](https://huggingface.co/blog/ibm-granite/granite-4-speech)
- [google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)
- Local test, 2026-10-16: Parakeet Redux on Windows CPU (project memory).
