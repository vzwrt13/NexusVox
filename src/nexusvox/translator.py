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

from .config import TranslatorConfig

logger = logging.getLogger(__name__)


def translate(text: str, config: TranslatorConfig) -> str:
    """The English version of `text`, or `text` itself when translation is off,
    the text is blank, or the translator cannot be reached."""
    if not config.enabled or not text.strip():
        return text
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
        return text
    logger.info("Translated (%s, %d ms): %r -> %r", result.get("source", "?"), result.get("ms", -1), text, translated)
    return translated
