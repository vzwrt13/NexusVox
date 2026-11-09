"""`[voice_commands]`: which spoken commands are active."""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_SYMBOLS: list[str] = [
    "slash",
    "backslash",
    "pipe",
    "tilde",
    "asterisk",
    "open paren",
    "close paren",
    "open bracket",
    "close bracket",
    "open brace",
    "close brace",
]


# "tab" is opt-in: "open a new tab" is an everyday sentence, and so are "less than" / "greater than".
_DEFAULT_STRUCTURAL = ["new paragraph", "new line", "all caps"]


@dataclass
class VoiceCommandsConfig:
    enabled: bool = True
    symbols: list[str] = field(default_factory=lambda: list(_DEFAULT_SYMBOLS))
    numbers_as_digits: bool = True
    bypass_symbols: bool = False
    # Structural commands (new line, new paragraph, tab, all caps): own category, own list, own bypass.
    structural: list[str] = field(default_factory=lambda: list(_DEFAULT_STRUCTURAL))
    bypass_structural: bool = False
    # Trailing "press enter" / "Enter drücken" presses Enter after the paste. Opt-in.
    press_enter: bool = False
