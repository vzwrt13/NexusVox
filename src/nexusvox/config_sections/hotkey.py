"""`[hotkey]`: the dictation binding, its key names and validation, and the default bindings of both hotkeys."""

from __future__ import annotations

from dataclasses import dataclass, field

# Default bindings, the single source for the dataclasses, the loader and the fallbacks. The legacy ones are what
# a config file written before the current defaults meant by a section that leaves the modifiers out.
DEFAULT_HOTKEY_MODIFIERS = ("ctrl", "win")
LEGACY_HOTKEY_MODIFIERS = ("ctrl", "shift", "alt")
DEFAULT_MEETING_MODIFIERS = ("ctrl", "shift")
LEGACY_MEETING_MODIFIERS = ("ctrl", "alt")
DEFAULT_MEETING_KEY = "F9"
LEGACY_MEETING_KEY = "M"


@dataclass
class HotkeyConfig:
    modifiers: list[str] = field(default_factory=lambda: list(DEFAULT_HOTKEY_MODIFIERS))
    # Optional non-modifier key that completes the combo (e.g. "a", "f9", "space").
    # Empty: the modifiers alone are the hotkey. Its keystrokes are swallowed while
    # it is held as part of the combo, so the target app never sees the letter.
    key: str = ""
    # "hold": record while the modifiers are held (push-to-talk).
    # "toggle": press once to start recording, press again to stop.
    mode: str = "hold"
    # Pressed during a recording with every modifier down: the recording is dropped and
    # the last pasted text is pasted again (Ctrl+Win+V). Empty turns it off.
    paste_key: str = "V"
    # Pressed during a recording with every modifier down: the selected text is rewritten by
    # the spoken instruction through the [llm] model (Ctrl+Win+P). Empty: off.
    polish_key: str = ""
    # Every recording ends by itself after this many seconds, as if the user had ended it
    # normally (a forgotten toggle recording would otherwise mute other apps forever). 0: no cap.
    max_recording_s: int = 600


HOTKEY_MODES = ("hold", "toggle")


HOTKEY_MODIFIERS = ("ctrl", "shift", "alt", "win")


# Non-modifier keys a hotkey may end with, name -> Win32 virtual-key code. Layout
# independent keys only: letters, digits, function keys and a few that never type.
HOTKEY_KEY_VKS: dict[str, int] = {
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    **{f"F{n}": 0x6F + n for n in range(1, 25)},
    "SPACE": 0x20,
    "CAPS_LOCK": 0x14,
    "SCROLL_LOCK": 0x91,
    "PAUSE": 0x13,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGE_UP": 0x21,
    "PAGE_DOWN": 0x22,
}


def normalize_hotkey_key(key: str) -> str | None:
    """Canonical (upper-case) key name, "" for none, None if unknown."""
    name = (key or "").strip().upper()
    if name == "":
        return ""
    return name if name in HOTKEY_KEY_VKS else None


def validate_hotkey(modifiers: list[str], key: str) -> str | None:
    """Error message for an unusable binding, None if it is fine."""
    if any(m not in HOTKEY_MODIFIERS for m in modifiers):
        return "Unknown modifier; use ctrl, shift, alt, win"
    if len(set(modifiers)) != len(modifiers):
        return "Duplicate modifier"
    if normalize_hotkey_key(key) is None:
        return f"Unknown key: {key}"
    if not modifiers and not normalize_hotkey_key(key):
        return "A hotkey needs at least one modifier or a key"
    return None
