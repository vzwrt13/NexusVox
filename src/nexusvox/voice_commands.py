"""Voice command processing for transcribed text.

Recognises spoken formatting commands (e.g. "new line", "tab", "all caps")
and replaces them with their corresponding characters or text transforms.
Symbol commands (e.g. "slash", "open paren") are opt-in via config.
Number conversion (e.g. "twenty five" → "25") is opt-in via config.
"""

from __future__ import annotations

import functools
import re

# Structural formatting commands — always active when voice commands are enabled
_STRUCTURAL_COMMANDS: dict[str, str] = {
    "new paragraph": "\n\n",
    "new line": "\n",
    "newline": "\n",
    "tab": "\t",
    "tabulator": "\t",
}

_ALL_CAPS = "all caps"

_SAFE_SYMBOLS: frozenset[str] = frozenset(
    {
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
        "less than",
        "greater than",
    }
)

# Symbol commands — active only when their keyword is in the enabled_symbols set
_SYMBOL_COMMANDS: dict[str, str] = {
    "slash": "/",
    "backslash": "\\",
    "pipe": "|",
    "tilde": "~",
    "asterisk": "*",
    "star": "*",
    "hash": "#",
    "percent": "%",
    "dash": "-",
    "hyphen": "-",
    "plus": "+",
    "equal": "=",
    "colon": ":",
    "open paren": "(",
    "close paren": ")",
    "open bracket": "[",
    "close bracket": "]",
    "open brace": "{",
    "close brace": "}",
    "less than": "<",
    "greater than": ">",
}

# Public list of all symbols for the dashboard UI.
# Safe symbols appear first; ambiguous (opt-in) symbols follow.
ALL_SYMBOL_INFO: list[dict] = [
    {"keyword": k, "char": v, "safe": k in _SAFE_SYMBOLS} for k, v in _SYMBOL_COMMANDS.items()
]

# ---------------------------------------------------------------------------
# Number conversion
# ---------------------------------------------------------------------------

_ONES: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALE: dict[str, int] = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}

_ALL_NUM_WORDS: set[str] = set(_ONES) | set(_TENS) | set(_SCALE)
_SORTED_NUM_WORDS = sorted(_ALL_NUM_WORDS, key=len, reverse=True)
_NUM_WORD_PAT = "(?:" + "|".join(re.escape(w) for w in _SORTED_NUM_WORDS) + ")"
# Match a maximal run of number words separated by spaces or hyphens
_NUM_SEQ_PATTERN: re.Pattern[str] = re.compile(
    r"\b" + _NUM_WORD_PAT + r"(?:[\s-]+" + _NUM_WORD_PAT + r")*\b",
    re.IGNORECASE,
)


def _words_to_int(words: list[str]) -> int:
    """Convert a list of number words to an integer value."""
    current = 0
    result = 0
    for w in words:
        lower = w.lower()
        if lower in _ONES:
            current += _ONES[lower]
        elif lower in _TENS:
            current += _TENS[lower]
        elif lower == "hundred":
            current = (current or 1) * 100
        elif lower in _SCALE:
            result += (current or 1) * _SCALE[lower]
            current = 0
    result += current
    return result


def _convert_numbers(text: str) -> str:
    """Replace runs of number words with their digit representation."""

    def replace_match(m: re.Match[str]) -> str:
        words = re.split(r"[\s-]+", m.group())
        return str(_words_to_int(words))

    return _NUM_SEQ_PATTERN.sub(replace_match, text)


# ---------------------------------------------------------------------------
# Symbol/structural command processing
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _build_pattern(
    active_symbols: frozenset[str],
) -> tuple[re.Pattern[str], dict[str, str], frozenset[str]]:
    """Build (and cache) the compiled regex, merged command dict, and symbol key set."""
    active_symbol_commands = {k: v for k, v in _SYMBOL_COMMANDS.items() if k in active_symbols}
    all_commands = {**_STRUCTURAL_COMMANDS, **active_symbol_commands}
    all_keywords = sorted(
        [*all_commands.keys(), _ALL_CAPS],
        key=len,
        reverse=True,
    )
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in all_keywords) + r")\b",
        re.IGNORECASE,
    )
    return pattern, all_commands, frozenset(active_symbol_commands)


# Pre-built pattern for structural-only mode (no symbols)
_STRUCTURAL_ONLY = frozenset()


def process_voice_commands(
    text: str,
    active_symbols: frozenset[str] | None = None,
    numbers_as_digits: bool = False,
) -> str:
    """Replace voice command phrases in *text* with their formatting effects.

    Case-insensitive matching with word boundaries to avoid partial matches
    (e.g. "tabletop" does not match "tab").

    The "all caps" command uppercases all subsequent text until the next
    structural command keyword or end of text. Symbol commands do not break
    all-caps mode.

    *active_symbols* is a frozenset of symbol keyword names to enable
    (e.g. ``frozenset({"slash", "open paren"})``). Pass ``None`` or an empty
    frozenset to use structural formatting commands only.

    When *numbers_as_digits* is ``True``, runs of number words (e.g.
    "twenty five") are replaced with digits ("25") after command processing.
    """
    if not text or not text.strip():
        return text

    symbol_set = active_symbols if active_symbols is not None else _STRUCTURAL_ONLY
    pattern, commands, symbol_keys = _build_pattern(symbol_set)
    tokens = pattern.split(text)
    result = _apply_commands(tokens, commands, symbol_keys)

    if numbers_as_digits:
        result = _convert_numbers(result)

    return result


def _strip_trailing_separator(text: str) -> str:
    """Strip trailing comma/space separator from text preceding a command."""
    if text.endswith(", "):
        return text[:-2]
    if text.endswith(","):
        return text[:-1]
    if text.endswith(" "):
        return text[:-1]
    return text


def _strip_leading_separator(text: str) -> str:
    """Strip leading comma/space separator from text following a command."""
    if text.startswith(", "):
        return text[2:]
    if text.startswith(","):
        return text[1:]
    if text.startswith(" "):
        return text[1:]
    return text


def _apply_commands(tokens: list[str], commands: dict[str, str], symbol_keys: frozenset[str]) -> str:
    """Walk split tokens, applying simple replacements and all-caps logic.

    Structural commands (new line, tab, etc.) reset the all-caps mode.
    Symbol commands (slash, pipe, etc.) do not — so "all caps hello slash world"
    produces "HELLO/WORLD" rather than "HELLO/world".
    """
    parts: list[str] = []
    caps_active = False

    for i, token in enumerate(tokens):
        lower = token.lower()

        if lower in commands:
            # Strip trailing comma/space from preceding text part
            if parts:
                parts[-1] = _strip_trailing_separator(parts[-1])
            parts.append(commands[lower])
            # Only structural commands (not symbols) break all-caps mode
            if lower not in symbol_keys:
                caps_active = False
            # Strip leading comma/space from following text token
            if i + 1 < len(tokens):
                tokens[i + 1] = _strip_leading_separator(tokens[i + 1])
            continue

        if lower == _ALL_CAPS:
            caps_active = True
            # Strip leading comma/space from following text token
            if i + 1 < len(tokens):
                tokens[i + 1] = _strip_leading_separator(tokens[i + 1])
            continue

        # Regular text segment
        if caps_active:
            parts.append(token.upper())
        else:
            parts.append(token)

    return "".join(parts)
