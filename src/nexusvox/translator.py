"""Translate a transcription into English before it is injected.

NexusVox stays a voice-input app: it does not translate, it asks a local translator
server (the personal-tooling `translator` tool, TranslateGemma via Ollama on port 8003)
and injects what comes back. The stored transcript is the spoken text, untouched;
only the injected text is translated. Any failure - server down, model missing,
timeout - returns the original text, so a sentence is never lost to translation.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import TranslatorConfig

logger = logging.getLogger(__name__)

_ERROR_LIMIT = 200
_PROBE_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class TranslateResult:
    """What to inject, and what happened on the way - the part that is saved."""

    text: str
    translated: bool = False
    source: str | None = None  # "de", "en" or "mixed" as the server saw it
    ms: int | None = None  # the server's own timing
    error: str | None = None  # set when the fallback to the original text fired


def translate(text: str, config: TranslatorConfig) -> TranslateResult:
    """The English version of `text`, or `text` itself when translation is off,
    the text is blank, or the translator cannot be reached."""
    if not config.enabled or not text.strip():
        return TranslateResult(text)
    body = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(config.url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            result = json.load(response)
        translated = result["text"]
        if not isinstance(translated, str):
            raise TypeError("translator returned no text")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Translator unavailable at %s (%s); injecting the original text", config.url, exc)
        return TranslateResult(text, error=f"{type(exc).__name__}: {exc}"[:_ERROR_LIMIT])
    source = result.get("source")
    ms = result.get("ms")
    logger.info("Translated (%s, %s ms): %r -> %r", source, ms, text, translated)
    return TranslateResult(
        translated,
        translated=True,
        source=source if isinstance(source, str) else None,
        ms=ms if isinstance(ms, int) else None,
    )


def is_reachable(config: TranslatorConfig) -> bool:
    """Whether a translator answers at `config.url` right now. Sends an empty
    translation so the check exercises the real endpoint, not just the port; any
    HTTP answer counts, because a running server may still reject the empty body."""
    body = json.dumps({"text": ""}).encode("utf-8")
    request = urllib.request.Request(config.url, body, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=min(config.timeout_s, _PROBE_TIMEOUT_S)).close()
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    return True
