"""Voice command processing for transcribed text.

Recognises spoken formatting commands (e.g. "new line", "tab", "all caps")
and replaces them with their corresponding characters or text transforms.
Structural commands and symbol commands are separate categories, each with
its own opt-in list. Number conversion (e.g. "twenty five" → "25") is opt-in
via config. Every vocabulary covers English and German; there is no language
switch, both are always recognised.
"""

from __future__ import annotations

import functools
import re

# Structural formatting commands. Canonical keyword -> (replacement, extra spoken aliases).
# The canonical keyword is what the config list and the dashboard use; aliases (including
# the German phrases) are recognised whenever the canonical keyword is enabled.
_STRUCTURAL_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "new paragraph": ("\n\n", ("neuer absatz", "neuen absatz")),
    "new line": ("\n", ("newline", "neue zeile", "zeilenumbruch")),
    "tab": ("\t", ("tabulator", "tabstopp")),
}

# "all caps" is structural too (it is a toggle, not a character), listed separately.
_ALL_CAPS = "all caps"
_ALL_CAPS_ALIASES: tuple[str, ...] = ("alles groß", "alles gross")

_STRUCTURAL_DISPLAY: dict[str, str] = {
    "new paragraph": "¶",
    "new line": "↵",
    "tab": "⇥",
    _ALL_CAPS: "ABC",
}

# Public list of structural commands for the dashboard UI.
ALL_STRUCTURAL_INFO: list[dict] = [
    {"keyword": k, "char": _STRUCTURAL_DISPLAY[k], "aliases": list(_STRUCTURAL_COMMANDS[k][1])}
    for k in _STRUCTURAL_COMMANDS
] + [{"keyword": _ALL_CAPS, "char": _STRUCTURAL_DISPLAY[_ALL_CAPS], "aliases": list(_ALL_CAPS_ALIASES)}]

ALL_STRUCTURAL_KEYWORDS: frozenset[str] = frozenset(i["keyword"] for i in ALL_STRUCTURAL_INFO)

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

# German phrases per symbol keyword; recognised whenever the English keyword is enabled.
_SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "slash": ("schrägstrich",),
    "backslash": ("umgekehrter schrägstrich",),
    "pipe": ("senkrechter strich",),
    "asterisk": ("sternchen",),
    "star": ("stern",),
    "hash": ("raute",),
    "percent": ("prozent",),
    "dash": ("gedankenstrich",),
    "hyphen": ("bindestrich",),
    "equal": ("gleich",),
    "colon": ("doppelpunkt",),
    "open paren": ("klammer auf",),
    "close paren": ("klammer zu",),
    "open bracket": ("eckige klammer auf",),
    "close bracket": ("eckige klammer zu",),
    "open brace": ("geschweifte klammer auf",),
    "close brace": ("geschweifte klammer zu",),
    "less than": ("kleiner als",),
    "greater than": ("größer als",),
}

# Public list of all symbols for the dashboard UI.
# Safe symbols appear first; ambiguous (opt-in) symbols follow.
ALL_SYMBOL_INFO: list[dict] = [
    {"keyword": k, "char": v, "safe": k in _SAFE_SYMBOLS, "aliases": list(_SYMBOL_ALIASES.get(k, ()))}
    for k, v in _SYMBOL_COMMANDS.items()
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


_WORD_RE: re.Pattern[str] = re.compile(r"[^\W\d_]+")
# Horizontal whitespace or hyphen only: a number run must not span a line break.
_SEP_RE: re.Pattern[str] = re.compile(r"(?:[^\S\r\n]|-)+")

_DE_UNITS: dict[str, int] = {
    "null": 0,
    "ein": 1,
    "eine": 1,
    "eins": 1,
    "zwei": 2,
    "zwo": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
}
_DE_TEENS: dict[str, int] = {
    "zehn": 10,
    "elf": 11,
    "zwölf": 12,
    "dreizehn": 13,
    "vierzehn": 14,
    "fünfzehn": 15,
    "sechzehn": 16,
    "siebzehn": 17,
    "achtzehn": 18,
    "neunzehn": 19,
}
_DE_TENS: dict[str, int] = {
    "zwanzig": 20,
    "dreißig": 30,
    "dreissig": 30,
    "vierzig": 40,
    "fünfzig": 50,
    "sechzig": 60,
    "siebzig": 70,
    "achtzig": 80,
    "neunzig": 90,
}
# Scale words that need a multiplier in front ("zwei Millionen"); hundert/tausend stand alone.
_DE_BIG_SCALES: dict[str, int] = {
    "million": 1_000_000,
    "millionen": 1_000_000,
    "milliarde": 1_000_000_000,
    "milliarden": 1_000_000_000,
}
# Inflected forms of "ein" are articles in German ("ein Haus"); they only count as a number
# directly in front of a scale word ("ein hundert", "eine Million").
_DE_ARTICLES: frozenset[str] = frozenset({"ein", "eine", "einen", "einem", "einer", "eines"})


def _de_below_100(s: str) -> int | None:
    if s in _DE_UNITS:
        return _DE_UNITS[s]
    if s in _DE_TEENS:
        return _DE_TEENS[s]
    if s in _DE_TENS:
        return _DE_TENS[s]
    unit, sep, tens = s.partition("und")
    if sep and 1 <= _DE_UNITS.get(unit, 0) <= 9 and tens in _DE_TENS:
        return _DE_UNITS[unit] + _DE_TENS[tens]
    return None


def _de_below_1000(s: str) -> int | None:
    if "hundert" not in s:
        return _de_below_100(s)
    head, _, tail = s.partition("hundert")
    hundreds = 1 if not head else _de_below_100(head)
    rest = 0 if not tail else _de_below_100(tail)
    if not hundreds or rest is None:
        return None
    return hundreds * 100 + rest


def _parse_de_compound(word: str) -> int | None:
    """Parse one German number word ("fünfundzwanzig", "zweihundertdreiundvierzig")."""
    rest = word
    total = 0
    matched = False
    for pattern, mult, head_required in (
        (r"milliarden?", 1_000_000_000, True),
        (r"millionen?", 1_000_000, True),
        (r"tausend", 1_000, False),
    ):
        m = re.search(pattern, rest)
        if not m:
            continue
        head = rest[: m.start()]
        value = _de_below_1000(head) if head else (None if head_required else 1)
        if value is None:
            return None
        total += value * mult
        rest = rest[m.end() :]
        matched = True
    if rest:
        value = _de_below_1000(rest)
        if value is None:
            return None
        return total + value
    return total if matched else None


def _de_classify(word: str) -> tuple[str, int] | None:
    """Classify a lower-case word as ("article" | "big" | "num", value), or None."""
    if word in _DE_ARTICLES:
        return ("article", 1)
    if word in _DE_BIG_SCALES:
        return ("big", _DE_BIG_SCALES[word])
    value = _parse_de_compound(word)
    return ("num", value) if value is not None else None


def _de_run_value(run: list[tuple[str, str, int]]) -> int:
    """Combine a run of (word, kind, value) German tokens into one integer."""
    result = 0
    current = 0
    for word, kind, value in run:
        if kind == "big":
            result += (current or 1) * value
            current = 0
        elif word == "hundert":
            current = (current or 1) * 100
        elif word == "tausend":
            result += (current or 1) * 1000
            current = 0
        else:
            result += value - value % 1000
            current += value % 1000
    return result + current


def _convert_numbers_de(text: str) -> str:
    """Replace runs of German number words with digits ("fünfundzwanzig" → "25")."""
    matches = list(_WORD_RE.finditer(text))
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(matches):
        run: list[tuple[str, str, int]] = []
        j = i
        while j < len(matches):
            m = matches[j]
            if run and not _SEP_RE.fullmatch(text[matches[j - 1].end() : m.start()]):
                break
            word = m.group().lower()
            info = _de_classify(word)
            if info is None:
                break
            if info[0] == "article":
                nxt = matches[j + 1] if j + 1 < len(matches) else None
                nxt_word = nxt.group().lower() if nxt else ""
                if not (
                    nxt
                    and _SEP_RE.fullmatch(text[m.end() : nxt.start()])
                    and (nxt_word in _DE_BIG_SCALES or nxt_word in ("hundert", "tausend"))
                ):
                    break
            if run and info[0] == "num" and word not in ("hundert", "tausend"):
                # Two standalone numbers only form one number if the second fits into the zeros
                # of the first ("zwanzig fünf", "hundert dreißig"); "zwei drei" stays two numbers.
                prev_value = run[-1][2]
                places = 1
                while prev_value and prev_value % (places * 10) == 0:
                    places *= 10
                if info[1] >= places:
                    break
            run.append((word, info[0], info[1]))
            j += 1
        # A big scale needs a multiplier in front of it ("Million" alone is not a number).
        if not run or run[0][1] == "big":
            i += 1
            continue
        out.append(text[pos : matches[i].start()])
        out.append(str(_de_run_value(run)))
        pos = matches[j - 1].end()
        i = j
    out.append(text[pos:])
    return "".join(out)


def _convert_numbers_en(text: str) -> str:
    """Replace runs of English number words with their digit representation."""

    def replace_match(m: re.Match[str]) -> str:
        words = re.split(r"[\s-]+", m.group())
        return str(_words_to_int(words))

    return _NUM_SEQ_PATTERN.sub(replace_match, text)


def _convert_numbers(text: str) -> str:
    """Replace number words with digits, English and German alike."""
    return _convert_numbers_en(_convert_numbers_de(text))


# ---------------------------------------------------------------------------
# Symbol/structural command processing
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=64)
def _build_pattern(
    active_symbols: frozenset[str],
    active_structural: frozenset[str],
) -> tuple[re.Pattern[str], dict[str, str], frozenset[str], frozenset[str]]:
    """Build (and cache) the regex, the phrase→character dict, the symbol phrases and the all-caps phrases."""
    commands: dict[str, str] = {}
    for keyword, (char, aliases) in _STRUCTURAL_COMMANDS.items():
        if keyword in active_structural:
            for phrase in (keyword, *aliases):
                commands[phrase] = char
    symbol_phrases: set[str] = set()
    for keyword, char in _SYMBOL_COMMANDS.items():
        if keyword in active_symbols:
            for phrase in (keyword, *_SYMBOL_ALIASES.get(keyword, ())):
                commands[phrase] = char
                symbol_phrases.add(phrase)
    caps_phrases = frozenset((_ALL_CAPS, *_ALL_CAPS_ALIASES)) if _ALL_CAPS in active_structural else frozenset()
    keywords = sorted([*commands, *caps_phrases], key=len, reverse=True)
    if not keywords:
        return re.compile(r"(?!)"), commands, frozenset(), caps_phrases
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b", re.IGNORECASE)
    return pattern, commands, frozenset(symbol_phrases), caps_phrases


def process_voice_commands(
    text: str,
    active_symbols: frozenset[str] | None = None,
    numbers_as_digits: bool = False,
    active_structural: frozenset[str] | None = None,
) -> str:
    """Replace voice command phrases in *text* with their formatting effects.

    Case-insensitive matching with word boundaries to avoid partial matches
    (e.g. "tabletop" does not match "tab"). English and German phrases are
    always both recognised.

    The "all caps" command uppercases all subsequent text until the next
    structural command keyword or end of text. Symbol commands do not break
    all-caps mode.

    *active_symbols* is a frozenset of symbol keyword names to enable
    (e.g. ``frozenset({"slash", "open paren"})``). ``None`` or an empty
    frozenset enables no symbols.

    *active_structural* is a frozenset of structural keywords to enable
    (``new line``, ``new paragraph``, ``tab``, ``all caps``). ``None`` enables
    all of them; an empty frozenset enables none.

    When *numbers_as_digits* is ``True``, runs of number words (e.g.
    "twenty five", "fünfundzwanzig") are replaced with digits ("25") after
    command processing.
    """
    if not text or not text.strip():
        return text

    symbol_set = active_symbols if active_symbols is not None else frozenset()
    structural_set = active_structural if active_structural is not None else ALL_STRUCTURAL_KEYWORDS
    pattern, commands, symbol_keys, caps_keys = _build_pattern(symbol_set, structural_set)
    result = _apply_commands(pattern.split(text), commands, symbol_keys, caps_keys)

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


def _apply_commands(
    tokens: list[str],
    commands: dict[str, str],
    symbol_keys: frozenset[str],
    caps_keys: frozenset[str],
) -> str:
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

        if lower in caps_keys:
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
