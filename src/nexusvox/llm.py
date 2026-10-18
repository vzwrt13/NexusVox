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
import urllib.error
import urllib.request

from .config import LlmConfig

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 1.5


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


def is_reachable(config: LlmConfig) -> bool:
    """Whether anything answers at the server address (any HTTP status counts), probed briefly."""
    try:
        urllib.request.urlopen(config.url, timeout=_PROBE_TIMEOUT_S).close()
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        return False
    return True
