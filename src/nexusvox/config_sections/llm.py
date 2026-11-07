"""`[llm]`, `[cleanup]` and `[polish]`: the language model and the features that use it."""

from __future__ import annotations

from dataclasses import dataclass, field

LLM_PROTOCOLS = ("ollama", "openai")
# Where Ollama listens on this PC. The dashboard lists it as a fixed entry, never as a saved custom server.
OLLAMA_URL = "http://127.0.0.1:11434"


def is_builtin_ollama(url: str, protocol: str) -> bool:
    """Whether *url* with *protocol* is Ollama at its default address on this PC."""
    return protocol == "ollama" and url.strip().rstrip("/") == OLLAMA_URL


# Preselected when `[llm].model` is not set and there is no CUDA GPU (docs/llm-cpu-test.md).
LLM_MODEL_CPU = "gemma4:e2b"


@dataclass
class LlmServer:
    """A language model server the user saved in the dashboard (`[[llm.servers]]`). `url` and `protocol` are
    what `[llm]` takes when one of its models is picked, `name` is the label of its group in the dropdown."""

    url: str
    protocol: str = "ollama"
    name: str = ""
    api_key: str = ""


@dataclass
class LlmFastModel:
    """The fast model of `[llm.fast]`: server and model, picked like the main model. An empty `model` means none was
    picked, and the tasks of the fast role go to the main model."""

    url: str = ""
    protocol: str = "ollama"
    model: str = ""
    api_key: str = ""


# The tasks of the core that ask the language model, in the order the dashboard lists them. Each one can be moved
# between the two roles while `[llm].per_task` is on. Extensions and Pro features (snippets, summaries, search and
# ask) read `[llm]` itself, so they always use the main model.
LLM_TASKS = ("dictation_cleanup", "polish", "file_cleanup")
# What the fast model does when nothing else was chosen: only the cleanup of a dictation has a timeout of seconds.
DEFAULT_FAST_TASKS = ("dictation_cleanup",)


@dataclass
class LlmConfig:
    """The language model server polish talks to. Not part of NexusVox: Ollama (default)
    or any OpenAI-compatible server. `keep_alive` (Ollama only) is how long the model
    stays loaded after a request: "60m" (default) is renewed in the background while NexusVox runs, so no
    dictation loses its cleanup after a pause, and the model expires on its own within an hour after a crash;
    "-1" never expires, a shorter duration frees the VRAM sooner.
    `servers` are the extra servers saved in the dashboard; their models fill the dropdown too.

    `url`, `protocol`, `model` and `api_key` are the main model, the only one while `per_task` is off. With
    `per_task` on, the tasks in `fast_tasks` go to the `fast` model instead (`llm.config_for`); `timeout_s` and
    `keep_alive` apply to both."""

    url: str = OLLAMA_URL
    protocol: str = "ollama"
    model: str = "gemma4:e4b"  # without a CUDA GPU and with no choice made: LLM_MODEL_CPU
    api_key: str = ""
    timeout_s: float = 60.0
    keep_alive: str = "60m"
    servers: list[LlmServer] = field(default_factory=list)
    per_task: bool = False
    fast: LlmFastModel = field(default_factory=LlmFastModel)
    fast_tasks: list[str] = field(default_factory=lambda: list(DEFAULT_FAST_TASKS))


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
