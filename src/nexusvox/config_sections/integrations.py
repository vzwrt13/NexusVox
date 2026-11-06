"""`[assistant]`, `[os_control]`, `[translator]`, `[remote_mic]` and `[support]`: what NexusVox talks to."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AssistantConfig:
    """Where "nexus assistant <text>" is forwarded: a local TCP port that answers
    `send <service> <text>` (the personal-tooling Controller's control port)."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 49730
    service: str = "voice-assistant"
    timeout_s: float = 5.0


# Keys that can mark a recording as an os-control command, pressed as the last key of the
# hotkey chord (Ctrl+Win+Y by default). OEM_102 is the "<" key left of Y/Z on a
# German (ISO) keyboard.
COMMAND_KEY_VKS: dict[str, int] = {"Y": 0x59, "OEM_102": 0xE2}


@dataclass
class OsControlConfig:
    """Command recordings: pressing `command_key` while the voice hotkey is held sends
    that recording's transcript to a local TCP port as `send <service> <text>` instead of
    typing it (the personal-tooling Controller's os-control service)."""

    enabled: bool = False
    command_key: str = "Y"
    host: str = "127.0.0.1"
    port: int = 49730
    service: str = "os-control"
    timeout_s: float = 5.0


@dataclass
class TranslatorConfig:
    """Translate the text into English before injecting it, via a local HTTP
    translator (the personal-tooling `translator` tool). The stored transcript stays
    in the spoken language; only the injected text is translated."""

    enabled: bool = False
    url: str = "http://127.0.0.1:8003/translate"
    timeout_s: float = 8.0


@dataclass
class RemoteMicConfig:
    """Let a phone use NexusVox as its speech recogniser over a WebSocket: it streams
    audio, gets the processed text back and inserts it itself. Off by default; it needs a
    token, and `host` should be a private address (a Tailscale IP). See docs/REMOTE_MIC.md."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 47393
    token: str = ""


@dataclass
class SupportConfig:
    """`feedback_form_url`: the page the dashboard's "Send feedback" button opens in the browser
    (app version, Windows version and hardware tier are added as URL parameters). Defaults to
    the live form; an empty value hides the button."""

    feedback_form_url: str = "https://nexusvox.app/feedback"
