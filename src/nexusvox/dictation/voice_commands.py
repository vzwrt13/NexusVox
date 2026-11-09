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
# Press enter
# ---------------------------------------------------------------------------

# Spoken at the very end of a dictation, these press Enter after the text is pasted.
PRESS_ENTER_PHRASES: tuple[str, ...] = ("press enter", "hit enter", "enter drücken", "drück enter", "drücke enter")

_PRESS_ENTER_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|(?<=[\s,.;:!?\-–—\"'“„(]))(?:"
    + "|".join(r"\s+".join(re.escape(w) for w in p.split()) for p in PRESS_ENTER_PHRASES)
    + r")[\s.,;:!?…\"'“”)]*$",
    re.IGNORECASE,
)


def split_press_enter(text: str) -> tuple[str, bool]:
    """Strip a trailing "press enter" phrase from *text*.

    Returns the remaining text and whether the phrase was found. Only the end of
    the dictation counts, so "press enter to continue" mid-sentence stays text.
    A comma, dash or opening quote before the phrase is dropped; sentence
    punctuation is kept ("Done. Press enter." -> "Done.").
    """
    m = _PRESS_ENTER_PATTERN.search(text)
    if not m:
        return text, False
    return text[: m.start()].rstrip(" ,;:-–—\"'“„("), True


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

# English ordinal word -> its cardinal ("twelfth" -> "twelve").
_IRREGULAR_ORD_EN: dict[str, str] = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}
_ORD_EN: dict[str, str] = {}
for _card in (*_ONES, *_TENS, *_SCALE):
    if _card == "zero":
        continue
    _ord = _IRREGULAR_ORD_EN.get(_card) or (_card[:-1] + "ieth" if _card.endswith("y") else _card + "th")
    _ORD_EN[_ord] = _card
_ORD_WORD_PAT = "(?:" + "|".join(re.escape(w) for w in sorted(_ORD_EN, key=len, reverse=True)) + ")"
# Number words are separated by horizontal whitespace or a hyphen only: a run never spans a line break.
_NUM_SEP = r"(?:[^\S\r\n]|-)+"
_SEP_RE: re.Pattern[str] = re.compile(_NUM_SEP)
_SEP_CAPTURE_RE: re.Pattern[str] = re.compile("(" + _NUM_SEP + ")")
# Match a maximal run of number words, optionally ending in an ordinal; _en_chunks cuts it into numbers.
_NUM_SEQ = _NUM_WORD_PAT + r"(?:" + _NUM_SEP + _NUM_WORD_PAT + r")*"
_NUM_SEQ_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:(?:" + _NUM_SEQ + _NUM_SEP + r")?" + _ORD_WORD_PAT + r"|" + _NUM_SEQ + r")\b",
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


_DE_ORDINAL_RE: re.Pattern[str] = re.compile(r"(.+t)(?:e|en|em|er|es)")
# Ordinal stems that are not the cardinal plus "t"/"st"; the tail is swapped for the cardinal's.
_DE_IRREGULAR_STEMS: tuple[tuple[str, str], ...] = (
    ("erst", "eins"),
    ("dritt", "drei"),
    ("siebt", "sieben"),
    ("acht", "acht"),
)
# "achte"/"achten" are also forms of the verb "achten" ("ich achte darauf"); they only count as an
# ordinal right after one of these words ("der achte Mai", "am achten").
_DE_ORDINAL_DETERMINERS: frozenset[str] = frozenset(
    {"der", "die", "das", "dem", "den", "des", "am", "im", "zum", "zur", "vom", "beim", "ein", "eine", "einen", "einem"}
)


def _de_ordinal_value(word: str, previous: str) -> int | None:
    """Value of a lower-case German ordinal ("zwölfter" -> 12, "zwanzigsten" -> 20), or None."""
    m = _DE_ORDINAL_RE.fullmatch(word)
    if not m:
        return None
    if word in ("achte", "achten") and previous not in _DE_ORDINAL_DETERMINERS:
        return None
    stem = m.group(1)
    candidates: list[tuple[str, str]] = [("t", stem[:-1]), ("st", stem[:-2])]
    for tail, cardinal in _DE_IRREGULAR_STEMS:
        if stem.endswith(tail):
            candidates.append(("irregular", stem[: -len(tail)] + cardinal))
    for kind, cardinal in candidates:
        value = _parse_de_compound(cardinal) if cardinal else None
        if value is None or value < 1:
            continue
        # "dreiste" (cheeky) and "einte" (united) are not ordinals: "-te" is for 2..19, "-ste" for 20 and up.
        if kind == "irregular" or (kind == "t" and 1 < value % 100 < 20) or (kind == "st" and value >= 20):
            return value
    return None


def _de_is_plain_eins(text: str, matches: list[re.Match[str]], i: int) -> bool:
    """True for a capitalised "Eins" opening a sentence that goes on with an ordinary word."""
    word = matches[i].group()
    if word != "Eins":
        return False
    before = text[: matches[i].start()].rstrip(" \t")
    if before and before[-1] not in ".!?\n":
        return False
    if i + 1 >= len(matches) or not _SEP_RE.fullmatch(text[matches[i].end() : matches[i + 1].start()]):
        return False
    return _de_classify(matches[i + 1].group().lower()) is None


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


# German number words that are English words too. They convert in German text only.
_DE_HOMOGRAPHS: frozenset[str] = frozenset({"null", "elf"})
_DE_HOMOGRAPH_RE: re.Pattern[str] = re.compile(r"\b(?:" + "|".join(_DE_HOMOGRAPHS) + r")\b", re.IGNORECASE)
# Words right before "elf"/"null" that mark German even in a short or mixed dictation ("Meeting um elf").
_DE_NUMBER_CONTEXT: frozenset[str] = frozenset(
    {"um", "bis", "gegen", "ab", "von", "vor", "nach", "mit", "für", "habe", "haben", "hat", "ist", "sind", "etwa"}
)


def _de_homograph_is_number(matches: list[re.Match[str]], j: int) -> bool:
    """Whether "elf"/"null" at ``matches[j]`` is a number although the text reads as English."""
    before = matches[j - 1].group().lower() if j else ""
    after = matches[j + 1].group().lower() if j + 1 < len(matches) else ""
    return before in _DE_NUMBER_CONTEXT or after == "uhr"


def _convert_numbers_de(text: str, homographs_as_words: bool = False) -> str:
    """Replace runs of German number words with digits ("fünfundzwanzig" → "25").

    With *homographs_as_words*, "null" and "elf" stay words unless a German word next to them says
    otherwise (the text reads as English: "returns null", "the elf ran").
    """
    matches = list(_WORD_RE.finditer(text))
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(matches):
        previous = matches[i - 1].group().lower() if i else ""
        ordinal = _de_ordinal_value(matches[i].group().lower(), previous)
        if ordinal is not None:
            out.append(text[pos : matches[i].start()])
            out.append(str(ordinal))
            pos = matches[i].end()
            # "Er wurde Zwölfter." keeps its single full stop; otherwise the ordinal brings one.
            if text[pos : pos + 1] != ".":
                out.append(".")
            i += 1
            continue
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
            if homographs_as_words and word in _DE_HOMOGRAPHS and not _de_homograph_is_number(matches, j):
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
        if not run or run[0][1] == "big" or (len(run) == 1 and _de_is_plain_eins(text, matches, i)):
            i += 1
            continue
        out.append(text[pos : matches[i].start()])
        out.append(str(_de_run_value(run)))
        pos = matches[j - 1].end()
        i = j
    out.append(text[pos:])
    return "".join(out)


def _en_ordinal_suffix(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _en_chunks(words: list[str]) -> list[list[str]]:
    """Cut a run of English number words into the numbers it spells ("one two three" -> three numbers).

    A word continues the current number only where English grammar allows it ("twenty five",
    "five thousand three hundred"); otherwise it starts the next number. So a list stays separate
    numbers and "nineteen eighty four" is 19 and 84, as the German path treats "zwei drei". A word
    that is followed by "hundred" or a scale word it cannot be added to ("two hundred three hundred",
    "one thousand two thousand") starts the next number as well.
    """
    cards = [_ORD_EN.get(w.lower(), w.lower()) for w in words]  # an ordinal fits where its cardinal fits
    chunks: list[list[str]] = []
    group = 0  # value of the current group below 1000
    last_scale = 0  # the last scale word (thousand, million, ...) of the current number; 0: none yet
    last_tens = False  # the last word was a tens word: a digit 1-9 may follow ("twenty five")
    closed = False  # "zero" stands alone
    for k, word in enumerate(words):
        card = cards[k]
        following = cards[k + 1] if k + 1 < len(cards) else ""
        fits = False
        if chunks and not closed:
            starts_group = (group == 0 and last_scale > 0) or (group > 0 and group % 100 == 0)
            # The word would multiply the next one, which the current number has no room for.
            multiplies_elsewhere = (following == "hundred" and group >= 100) or (
                following in _SCALE and following != "hundred" and last_scale > 0 and _SCALE[following] >= last_scale
            )
            if card == "zero":
                fits = False
            elif card == "hundred":
                fits = 0 < group < 100
            elif card in _SCALE:
                fits = group > 0 and (not last_scale or _SCALE[card] < last_scale)
            elif card in _TENS or _ONES[card] >= 10:
                fits = starts_group and not multiplies_elsewhere
            else:  # 1..9
                fits = (starts_group or last_tens) and not multiplies_elsewhere
        if fits:
            chunks[-1].append(word)
        else:
            chunks.append([word])
            group, last_scale, closed = 0, 0, False
        last_tens = card in _TENS
        if card == "zero":
            closed = True
        elif card == "hundred":
            group = (group or 1) * 100
        elif card in _SCALE:
            last_scale, group = _SCALE[card], 0
        else:
            group += _TENS.get(card) or _ONES[card]
    return chunks


def _en_number_text(chunk: list[str], follows_number: bool) -> str:
    """One number's words as digits; a trailing ordinal word makes it an ordinal ("twenty first" -> "21st").

    A lone ordinal stays a word where it is no position: "first" and "second" anywhere ("a second"), and
    any ordinal right behind another number ("one third of the users", "one second").
    """
    last = chunk[-1].lower()
    if last not in _ORD_EN:
        return str(_words_to_int(chunk))
    if len(chunk) == 1 and (follows_number or last in ("first", "second")):
        return chunk[0]
    n = _words_to_int([*chunk[:-1], _ORD_EN[last]])
    return f"{n}{_en_ordinal_suffix(n)}"


def _convert_numbers_en(text: str) -> str:
    """Replace runs of English number words with digits ("twenty five" -> "25", "twelfth" -> "12th")."""

    def replace_match(m: re.Match[str]) -> str:
        parts = _SEP_CAPTURE_RE.split(m.group())  # words at even places, the separators between them at odd ones
        words, seps = parts[0::2], parts[1::2]
        out: list[str] = []
        used = 0
        for n, chunk in enumerate(_en_chunks(words)):
            out.append(_en_number_text(chunk, follows_number=n > 0))
            used += len(chunk)
            if used < len(words):
                out.append(seps[used - 1])  # whatever stood between the two numbers (a tab, a hyphen) stays
        return "".join(out)

    return _NUM_SEQ_PATTERN.sub(replace_match, text)


def _is_english(text: str) -> bool:
    """Whether the language detector reads *text* as English (German is the only other candidate)."""
    from .lang_detect import language_guess  # lingua builds its detector on import: only numbers need it

    return language_guess(text)[0] == "en"


def _convert_numbers(text: str) -> str:
    """Replace number words with digits, English and German alike.

    Both languages are always recognised. Only "null" and "elf", which are English words too, need
    the text's language: in a text that reads as English they stay words ("returns null", "the elf
    ran") unless a German word next to them says otherwise. The language check runs only when one of
    the two is in the text.
    """
    homographs_as_words = _DE_HOMOGRAPH_RE.search(text) is not None and _is_english(text)
    return _convert_numbers_en(_convert_numbers_de(text, homographs_as_words))


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


# What the speech model puts after a spoken command: spaces, a comma, or sentence punctuation
# ("Hello. New line. World."). A full stop only counts when a space or the end follows it (".NET" stays).
_LEADING_SEPARATOR: re.Pattern[str] = re.compile(r"[ \t]*(?:,+|[,;:.…?!]+(?!\S))?[ \t]*")
# After a symbol the text goes on in the same sentence: only a comma and one space are dropped.
_LEADING_SYMBOL_SEPARATOR: re.Pattern[str] = re.compile(r", ?| ")


def _strip_leading_separator(text: str, punctuation: bool = True) -> str:
    """Strip what the speech model put between a command and the text after it.

    After a structural command that is spaces and sentence punctuation; after a symbol
    (*punctuation* false) only a comma and a space, so "foo close paren. Next" keeps its full stop.
    """
    m = (_LEADING_SEPARATOR if punctuation else _LEADING_SYMBOL_SEPARATOR).match(text)
    return text[m.end() :] if m else text


# "all caps" is a noun phrase after these words ("typed in all caps"), not a command.
_ALL_CAPS_AFTER: frozenset[str] = frozenset(
    {"in", "with", "without", "of", "the", "a", "an", "no", "not", "use", "using", "is", "are", "was", "were"}
    | {"im", "mit", "ohne", "der", "die", "das", "ein", "eine", "kein", "keine", "nicht", "ist", "sind"}
)
_PRECEDING_WORD: re.Pattern[str] = re.compile(r"(\w+)\s*$")
_ALL_CAPS_BEFORE: re.Pattern[str] = re.compile(r"\W*(?:geschrieben|schreiben|schreibt|schreibe)\b", re.IGNORECASE)


def _is_all_caps_text(tokens: list[str], i: int) -> bool:
    """Whether the "all caps" at ``tokens[i]`` is part of an ordinary sentence, so it stays text.

    That is the case right after a word such as "in" ("I wrote it in all caps"), before "geschrieben"
    ("alles groß geschrieben"), and when no word follows at all: the command would have nothing to
    uppercase and only swallow the words.
    """
    before = _PRECEDING_WORD.search(tokens[i - 1]) if i else None
    if before and before.group(1).lower() in _ALL_CAPS_AFTER:
        return True
    after = "".join(tokens[i + 1 :])
    return not re.search(r"\w", after) or _ALL_CAPS_BEFORE.match(after) is not None


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
                tokens[i + 1] = _strip_leading_separator(tokens[i + 1], punctuation=lower not in symbol_keys)
            continue

        if lower in caps_keys and not _is_all_caps_text(tokens, i):
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
