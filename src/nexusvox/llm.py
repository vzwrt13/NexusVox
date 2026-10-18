"""A chat-completion call to a local language model server the user runs.

NexusVox does not ship or load a language model. It sends one request to the
server configured under `[llm]`: Ollama's own API (default, `protocol = "ollama"`,
which can switch a reasoning model's thinking off and keep the model loaded for
`keep_alive`) or any OpenAI-compatible `/chat/completions` endpoint
(`protocol = "openai"`: LM Studio, llama.cpp server, vLLM, a cloud API).
Callers decide what to do on failure; `LlmError` covers every way it can fail.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import threading
import urllib.error
import urllib.request

from .config import LlmConfig

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 1.5

_FENCE_RE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)
# A reasoning model behind protocol = "openai" has no `think: false` and may put its thinking first.
_THINK_RE = re.compile(r"^<think>.*?</think>", re.DOTALL)


class LlmError(Exception):
    """The model server could not be reached or gave no usable answer."""


def _request(config: LlmConfig, system: str, user: str) -> tuple[str, dict]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    base = config.url.rstrip("/")
    if config.protocol == "openai":
        return f"{base}/chat/completions", {"model": config.model, "messages": messages, "temperature": 0.2}
    return f"{base}/api/chat", {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": config.keep_alive,
        "options": {"temperature": 0.2},
    }


def complete(config: LlmConfig, system: str, user: str) -> str:
    """The model's reply to one system + user message pair. Raises `LlmError`."""
    url, body = _request(config, system, user)
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            reply = json.load(response)
        if config.protocol == "openai":
            text = reply["choices"][0]["message"]["content"]
        else:
            text = reply["message"]["content"]
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        TimeoutError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        raise LlmError(f"{type(exc).__name__}: {exc}"[:200]) from exc
    if not isinstance(text, str) or not text.strip():
        raise LlmError("the model returned no text")
    return text.strip()


def clean_reply(reply: str) -> str:
    """Strip a leading thinking block and a code fence the model wrapped around its answer."""
    reply = _THINK_RE.sub("", reply.strip()).strip()
    m = _FENCE_RE.match(reply)
    return m.group(1).strip() if m else reply


def warm_up(config: LlmConfig) -> None:
    """Load the model in the background, so the first real request does not wait 10-50 s for
    it - auto cleanup gives up after a few seconds. Never raises; a failure is only logged."""

    def run() -> None:
        try:
            complete(config, "Reply with OK.", "OK")
            logger.info("Language model %s is loaded", config.model)
        except LlmError as exc:
            logger.info("Language model warm-up at %s failed (%s)", config.url, exc)

    threading.Thread(target=run, daemon=True, name="llm-warm-up").start()


def is_reachable(config: LlmConfig) -> bool:
    """Whether anything answers at the server address (any HTTP status counts), probed briefly."""
    try:
        urllib.request.urlopen(config.url, timeout=_PROBE_TIMEOUT_S).close()
    except urllib.error.HTTPError as exc:
        exc.close()
        return True
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        return False
    return True
