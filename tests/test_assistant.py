"""Tests for forwarding "nexus assistant <text>" to a local assistant service."""

from __future__ import annotations

import socket
import threading

import pytest

from nexusvox.assistant import parse_assistant_command, send_to_assistant
from nexusvox.config import AssistantConfig

# --- Parsing ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("nexus assistant remember to buy milk", "remember to buy milk"),
        ("Nexus Assistant, remember to buy milk.", "remember to buy milk."),
        ("nexus assistant: start gemini", "start gemini"),
        ("Nexus, Assistent notiere Milch kaufen", "notiere Milch kaufen"),
        ("  nexus   assistant   stop  ", "stop"),
    ],
)
def test_parse_assistant_command(text, expected):
    assert parse_assistant_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "nexus assistant",
        "nexus assistant   ",
        "I told the nexus assistant to remember milk",
        "nexus open chrome",
        "assistant remember milk",
        "",
    ],
)
def test_parse_assistant_command_rejects(text):
    assert parse_assistant_command(text) is None


# --- Sending ---


def _serve_once(reply: bytes) -> tuple[socket.socket, list[bytes]]:
    """A one-shot server on a free port: records the request, answers `reply`."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    received: list[bytes] = []

    def run():
        conn, _ = server.accept()
        with conn:
            chunks = []
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
            received.append(b"".join(chunks))
            conn.sendall(reply)
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return server, received


def test_send_to_assistant_roundtrip():
    server, received = _serve_once(b"sent to gemini")
    config = AssistantConfig(enabled=True, port=server.getsockname()[1], service="voice-assistant")

    reply = send_to_assistant("remember to buy milk", config)

    assert reply == "sent to gemini"
    assert received == [b"send voice-assistant remember to buy milk"]


def test_send_to_assistant_nothing_listening():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    config = AssistantConfig(enabled=True, port=free_port, timeout_s=0.5)

    with pytest.raises(OSError):
        send_to_assistant("remember to buy milk", config)
