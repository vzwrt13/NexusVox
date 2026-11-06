"""`[llm]`, `[cleanup]` and `[polish]`: the language model and the features that use it."""

from __future__ import annotations

from dataclasses import dataclass

LLM_PROTOCOLS = ("ollama", "openai")
# Preselected when `[llm].model` is not set and there is no CUDA GPU (docs/llm-cpu-test.md).
LLM_MODEL_CPU = "gemma4:e2b"


@dataclass
class LlmConfig:
    """The language model server polish talks to. Not part of NexusVox: Ollama (default)
    or any OpenAI-compatible server. `keep_alive` (Ollama only) is how long the model
    stays loaded after a request; shorter frees the VRAM sooner, longer avoids reloads."""

    url: str = "http://127.0.0.1:11434"
    protocol: str = "ollama"
    model: str = "gemma4:e4b"  # without a CUDA GPU and with no choice made: LLM_MODEL_CPU
    api_key: str = ""
    timeout_s: float = 60.0
    keep_alive: str = "10m"


CLEANUP_LEVELS = ("off", "light", "medium")


@dataclass
class CleanupConfig:
    """Tidy every dictation through the [llm] model before it is typed: `light` drops
    fillers and fixes punctuation, `medium` also grammar. `timeout_s` is short on purpose:
    a model still loading must not hold the dictation up; it is typed unchanged instead."""

    level: str = "off"
    timeout_s: float = 3.0


# `[cleanup].timeout_s` when unset and there is no CUDA GPU: the CPU needs 2-6 s (docs/llm-cpu-test.md).
CLEANUP_TIMEOUT_CPU_S = 10.0


@dataclass
class PolishConfig:
    """How polish reads the selection. In a terminal it needs Ctrl+C (the terminal does
    not report its selection), which interrupts the running program when nothing is
    selected, so terminals are skipped unless `in_terminals` is set."""

    in_terminals: bool = False
