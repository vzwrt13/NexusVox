"""Forward "nexus assistant <text>" to a voice assistant instead of typing it.

NexusVox stays dumb about the assistant: it only recognises the prefix, sends the rest of
the sentence to a local TCP port and logs the reply. The protocol is the one the
personal-tooling Controller speaks on its control port: one request per connection,
`send <service> <text>`, the client shuts its write side, the server answers with one
line of text and closes. What the text means - a note, "start", "stop" - is decided on
the other end.
"""

from __future__ import annotations

import logging
import re
import socket

from .config import AssistantConfig

logger = logging.getLogger(__name__)

# The whole utterance, so "I told the nexus assistant to ..." mid-sentence does not fire.
# "assistent" is what the models make of it in a German sentence.
_ASSISTANT_PATTERN = re.compile(
    r"^\s*nexus[,]?\s+assist[ae]nt[,:]?\s+(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_REPLY_LIMIT = 4096


def parse_assistant_command(text: str) -> str | None:
    """The text after "nexus assistant", or None if the utterance is not one."""
    m = _ASSISTANT_PATTERN.match(text)
    if not m:
        return None
    return m.group(1).strip() or None


def send_to_assistant(text: str, config: AssistantConfig) -> str:
    """Hand `text` to the assistant service. Returns its reply; raises OSError when
    nothing answers on the port, so the caller can fall back to typing the text."""
    request = f"send {config.service} {text}".encode()
    with socket.create_connection((config.host, config.port), timeout=config.timeout_s) as conn:
        conn.sendall(request)
        conn.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        total = 0
        while total < _REPLY_LIMIT:
            chunk = conn.recv(1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    reply = b"".join(chunks).decode("utf-8", "replace").strip()
    logger.info("Assistant command forwarded to %s:%d -> %s", config.host, config.port, reply or "(no reply)")
    return reply
