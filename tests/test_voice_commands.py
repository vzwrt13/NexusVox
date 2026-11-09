"""Tests for voice command processing."""

from __future__ import annotations

import pytest

from nexusvox.dictation.voice_commands import process_voice_commands, split_press_enter

# --- New line ---


def test_new_line_mid_sentence():
    assert process_voice_commands("hello new line world") == "hello\nworld"


def test_new_line_at_start():
    assert process_voice_commands("new line hello") == "\nhello"


def test_new_line_at_end():
    assert process_voice_commands("hello new line") == "hello\n"


def test_newline_single_word_alias():
    assert process_voice_commands("hello newline world") == "hello\nworld"


# --- New paragraph ---


def test_new_paragraph():
    assert process_voice_commands("first new paragraph second") == "first\n\nsecond"


# --- Tab ---


def test_tab_replacement():
    assert process_voice_commands("name tab value") == "name\tvalue"


def test_tab_no_partial_match():
    """'tabletop' should not trigger 'tab' command."""
    assert process_voice_commands("the tabletop is clean") == "the tabletop is clean"


# --- All caps ---


def test_all_caps_until_end():
    assert process_voice_commands("say all caps wow") == "say WOW"


def test_all_caps_multiple_words():
    assert process_voice_commands("all caps do not enter") == "DO NOT ENTER"


def test_all_caps_until_next_command():
    """'all caps' scope ends at the next command keyword."""
    assert process_voice_commands("all caps hello new line world") == "HELLO\nworld"


# --- Multiple commands ---


def test_multiple_commands_in_sequence():
    assert process_voice_commands("new line new line") == "\n\n"


def test_mixed_commands():
    result = process_voice_commands("tab all caps warning new line please read")
    assert result == "\tWARNING\nplease read"


# --- Case insensitivity ---


def test_case_insensitive():
    assert process_voice_commands("hello New Line world") == "hello\nworld"
    assert process_voice_commands("hello NEW LINE world") == "hello\nworld"


# --- Comma stripping around commands ---


def test_new_line_with_commas():
    assert process_voice_commands("hello, new line, world") == "hello\nworld"


def test_newline_with_commas():
    assert process_voice_commands("hello, newline, world") == "hello\nworld"


def test_new_paragraph_with_commas():
    assert process_voice_commands("first, new paragraph, second") == "first\n\nsecond"


def test_commas_in_normal_text_unaffected():
    assert process_voice_commands("hello, world") == "hello, world"


# --- Passthrough / edge cases ---


def test_no_commands_passthrough():
    assert process_voice_commands("just regular text") == "just regular text"


def test_empty_string():
    assert process_voice_commands("") == ""


def test_whitespace_only():
    assert process_voice_commands("   ") == "   "


def test_command_is_entire_text():
    assert process_voice_commands("new line") == "\n"


def test_all_caps_entire_text():
    assert process_voice_commands("all caps hello world") == "HELLO WORLD"


# --- Symbol commands (require active_symbols) ---

_SAFE = frozenset(
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
_ALL_SYMBOLS = _SAFE | frozenset({"hash", "percent", "dash", "hyphen", "plus", "colon", "star"})


def test_slash():
    assert process_voice_commands("path slash file", _SAFE) == "path/file"


def test_backslash():
    assert process_voice_commands("C colon backslash Users", frozenset({"colon", "backslash"})) == "C:\\Users"


def test_pipe():
    assert process_voice_commands("ls pipe grep", _SAFE) == "ls|grep"


def test_tilde():
    assert process_voice_commands("cd tilde", _SAFE) == "cd~"


def test_asterisk():
    assert process_voice_commands("rm asterisk", _SAFE) == "rm*"


def test_star_alias():
    assert process_voice_commands("rm star", _ALL_SYMBOLS) == "rm*"


def test_hash():
    assert process_voice_commands("hash include", frozenset({"hash"})) == "#include"


def test_percent():
    assert process_voice_commands("50 percent", frozenset({"percent"})) == "50%"


def test_dash():
    assert process_voice_commands("em dash", frozenset({"dash"})) == "em-"


def test_hyphen():
    assert process_voice_commands("well hyphen known", frozenset({"hyphen"})) == "well-known"


def test_plus():
    assert process_voice_commands("a plus b", frozenset({"plus"})) == "a+b"


def test_colon():
    assert process_voice_commands("key colon value", frozenset({"colon"})) == "key:value"


def test_open_paren():
    assert process_voice_commands("func open paren", _SAFE) == "func("


def test_close_paren():
    assert process_voice_commands("x close paren", _SAFE) == "x)"


def test_open_bracket():
    assert process_voice_commands("arr open bracket 0", _SAFE) == "arr[0"


def test_close_bracket():
    assert process_voice_commands("0 close bracket", _SAFE) == "0]"


def test_open_brace():
    assert process_voice_commands("dict open brace", _SAFE) == "dict{"


def test_close_brace():
    assert process_voice_commands("end close brace", _SAFE) == "end}"


def test_less_than():
    assert process_voice_commands("a less than b", _SAFE) == "a<b"


def test_greater_than():
    assert process_voice_commands("a greater than b", _SAFE) == "a>b"


def test_equal():
    assert process_voice_commands("x equal 5", frozenset({"equal"})) == "x=5"


def test_symbol_disabled_passthrough():
    """Symbol keywords not in active_symbols pass through unchanged."""
    assert process_voice_commands("a plus b") == "a plus b"
    assert process_voice_commands("em dash", _SAFE) == "em dash"


def test_symbol_with_all_caps():
    assert process_voice_commands("all caps hello slash world", _SAFE) == "HELLO/WORLD"


def test_structural_commands_unaffected_by_empty_symbols():
    """Structural commands always work regardless of active_symbols."""
    assert process_voice_commands("hello new line world", frozenset()) == "hello\nworld"


# --- Number conversion ---


def test_numbers_single_digit():
    assert process_voice_commands("chapter one", numbers_as_digits=True) == "chapter 1"


def test_numbers_teens():
    assert process_voice_commands("page thirteen", numbers_as_digits=True) == "page 13"


def test_numbers_tens():
    assert process_voice_commands("item twenty", numbers_as_digits=True) == "item 20"


def test_numbers_compound():
    assert process_voice_commands("twenty five", numbers_as_digits=True) == "25"


def test_numbers_hundreds():
    assert process_voice_commands("one hundred", numbers_as_digits=True) == "100"


def test_numbers_hundreds_compound():
    assert process_voice_commands("two hundred fifty", numbers_as_digits=True) == "250"


def test_numbers_thousands():
    assert process_voice_commands("one thousand five hundred", numbers_as_digits=True) == "1500"


def test_numbers_zero():
    assert process_voice_commands("zero", numbers_as_digits=True) == "0"


def test_numbers_disabled_passthrough():
    """Number words pass through unchanged when numbers_as_digits is False."""
    assert process_voice_commands("twenty five") == "twenty five"


def test_numbers_with_symbols():
    """Number conversion runs after symbol commands."""
    assert (
        process_voice_commands(
            "func open paren two close paren",
            _SAFE,
            numbers_as_digits=True,
        )
        == "func(2)"
    )


def test_numbers_hyphenated():
    assert process_voice_commands("twenty-five items", numbers_as_digits=True) == "25 items"


# ---------------------------------------------------------------------------
# German number words
# ---------------------------------------------------------------------------


def test_numbers_de_compound():
    assert process_voice_commands("Ich habe fünfundzwanzig Euro", numbers_as_digits=True) == "Ich habe 25 Euro"


def test_numbers_de_hundreds():
    assert process_voice_commands("zweihundertdreiundvierzig Leute", numbers_as_digits=True) == "243 Leute"


def test_numbers_de_spaced_scales():
    assert process_voice_commands("zwei tausend fünf hundert", numbers_as_digits=True) == "2500"
    assert process_voice_commands("dreitausend zwei hundert", numbers_as_digits=True) == "3200"


def test_numbers_de_million():
    assert process_voice_commands("eine Million zweihunderttausend", numbers_as_digits=True) == "1200000"


def test_numbers_de_year():
    assert process_voice_commands("neunzehnhundertneunundachtzig", numbers_as_digits=True) == "1989"


def test_numbers_de_simple_and_punctuation():
    assert process_voice_commands("drei Tage, einundzwanzig Uhr", numbers_as_digits=True) == "3 Tage, 21 Uhr"


def test_numbers_de_article_ein_untouched():
    text = "ein Haus und eine Katze"
    assert process_voice_commands(text, numbers_as_digits=True) == text


def test_numbers_de_disabled_passthrough():
    assert process_voice_commands("fünfundzwanzig Euro") == "fünfundzwanzig Euro"


def test_numbers_english_million_still_works_next_to_german_logic():
    assert process_voice_commands("I have two million", numbers_as_digits=True) == "I have 2000000"


# ---------------------------------------------------------------------------
# German structural and symbol phrases
# ---------------------------------------------------------------------------


def test_structural_de():
    assert process_voice_commands("hallo neue Zeile welt Tabulator x neuer Absatz y") == "hallo\nwelt\tx\n\ny"


def test_all_caps_de():
    assert process_voice_commands("alles groß hallo welt") == "HALLO WELT"


def test_symbols_de():
    active = frozenset({"open paren", "close paren", "slash"})
    assert process_voice_commands("f Klammer auf x Klammer zu Schrägstrich", active) == "f(x)/"


# ---------------------------------------------------------------------------
# Structural category is independent
# ---------------------------------------------------------------------------


def test_structural_empty_set_disables_all():
    text = "a new line b tab c all caps d"
    assert process_voice_commands(text, active_structural=frozenset()) == text


def test_structural_subset_only_enabled_fire():
    out = process_voice_commands("a new line b tab c", active_structural=frozenset({"tab"}))
    assert out == "a new line b\tc"


def test_structural_disabled_does_not_affect_symbols_or_numbers():
    out = process_voice_commands(
        "twenty five slash new line",
        frozenset({"slash"}),
        True,
        frozenset(),
    )
    assert out == "25/new line"


def test_numbers_de_adjacent_numbers_are_not_summed():
    assert process_voice_commands("eins zwei drei", numbers_as_digits=True) == "1 2 3"
    assert process_voice_commands("zwanzig fünf", numbers_as_digits=True) == "25"
    assert process_voice_commands("hundert dreißig", numbers_as_digits=True) == "130"


def test_numbers_de_do_not_span_a_new_line():
    assert process_voice_commands("zwei new line drei", numbers_as_digits=True) == "2\n3"


# ---------------------------------------------------------------------------
# Press enter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello world, press enter.", "Hello world"),
        ("Done. Press Enter.", "Done."),
        ("Ship it hit enter!", "Ship it"),
        ("Hallo Welt, Enter drücken.", "Hallo Welt"),
        ("Bin gleich da drück Enter", "Bin gleich da"),
        ("Okay, drücke Enter", "Okay"),
        ("press  enter", ""),
        ("Enter drücken.", ""),
        ("Send it, press enter,", "Send it"),
        ("Okay - hit enter…", "Okay"),
        ("Schick es „Enter drücken“", "Schick es"),
    ],
)
def test_split_press_enter_trailing_phrase(text, expected):
    assert split_press_enter(text) == (expected, True)


@pytest.mark.parametrize(
    "text",
    [
        "press enter to continue",
        "Drück Enter und warte",
        "depress enter",
        "just press",
        "",
    ],
)
def test_split_press_enter_leaves_other_text(text):
    assert split_press_enter(text) == (text, False)


# ---------------------------------------------------------------------------
# Ordinals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("zwölfter Test", "12. Test"),
        ("der zwölfte Mai", "der 12. Mai"),
        ("am zwanzigsten", "am 20."),
        ("der hundertste Kunde", "der 100. Kunde"),
        ("einundzwanzigste", "21."),
        ("die erste, dritte, siebte", "die 1., 3., 7."),
        ("am achten Mai", "am 8. Mai"),
        ("Er wurde Zwölfter.", "Er wurde 12."),
        ("die Neunte", "die 9."),
        ("Ludwig der Vierzehnte", "Ludwig der 14."),
    ],
)
def test_german_ordinals_become_digits(spoken, expected):
    assert process_voice_commands(spoken, numbers_as_digits=True) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "ich achte darauf",
        "wir achten darauf",
        "zweitens und drittens",
        "ein Drittel, ein Viertel, ein Achtel",
        "die dreiste Lüge",
        "er einte das Land",
        "Eins ist klar",
        "zwölfter Test",
    ],
)
def test_german_non_ordinals_and_switch_off_stay_words(spoken):
    if spoken == "zwölfter Test":
        assert process_voice_commands(spoken, numbers_as_digits=False) == spoken
    else:
        assert process_voice_commands(spoken, numbers_as_digits=True) == spoken


def test_eins_still_a_number_in_a_list():
    assert process_voice_commands("Eins, zwei, drei", numbers_as_digits=True) == "1, 2, 3"
    assert process_voice_commands("Eins zwei drei", numbers_as_digits=True) == "1 2 3"


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("twelfth", "12th"),
        ("twenty first", "21st"),
        ("twenty-second", "22nd"),
        ("the third", "the 3rd"),
        ("two hundredth", "200th"),
        ("a second", "a second"),
        ("first of all", "first of all"),
        ("one second", "1 second"),
    ],
)
def test_english_ordinals(spoken, expected):
    assert process_voice_commands(spoken, numbers_as_digits=True) == expected
    assert process_voice_commands(spoken) == spoken


# --- English number runs: one number per run that fits, a list stays a list ---


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("count one two three", "count 1 2 3"),
        ("nineteen eighty four", "19 84"),
        ("in twenty twenty", "in 20 20"),
        ("zero five", "0 5"),
        ("twenty five", "25"),
        ("twenty-one thousand three hundred forty two", "21342"),
        ("five thousand three hundred", "5300"),
        ("twenty one hundred", "2100"),
        ("two million five hundred thousand", "2500000"),
        ("one hundred twenty three four", "123 4"),
        ("twenty first century", "21st century"),
    ],
)
def test_english_number_runs_stop_where_the_next_word_does_not_fit(spoken, expected):
    assert process_voice_commands(spoken, numbers_as_digits=True) == expected


def test_english_number_run_never_crosses_a_line_break():
    assert process_voice_commands("one\ntwo", numbers_as_digits=True) == "1\n2"
    assert process_voice_commands("twenty\r\nfive", numbers_as_digits=True) == "20\r\n5"
    assert process_voice_commands("twenty five\nthirty", numbers_as_digits=True) == "25\n30"


def test_number_run_across_a_spoken_new_line_stays_two_numbers():
    assert process_voice_commands("one new line two", numbers_as_digits=True) == "1\n2"


# --- German number words in English text stay words ---


@pytest.mark.parametrize(
    "spoken",
    ["the function returns null here", "the elf ran", "I counted drei apples"],
)
def test_german_number_words_stay_in_english_text(spoken):
    assert process_voice_commands(spoken, numbers_as_digits=True) == spoken


def test_german_numbers_still_convert_in_german_text():
    assert process_voice_commands("Ich habe elf Katzen", numbers_as_digits=True) == "Ich habe 11 Katzen"
    assert process_voice_commands("Der Wert ist null", numbers_as_digits=True) == "Der Wert ist 0"


def test_english_numbers_still_convert_in_english_text_next_to_german_words():
    assert process_voice_commands("the elf ran twenty five miles", numbers_as_digits=True) == "the elf ran 25 miles"


# --- Defaults: everyday sentences stay text ---


def _with_default_config(text: str) -> str:
    from nexusvox.config import VoiceCommandsConfig

    cfg = VoiceCommandsConfig()
    return process_voice_commands(text, frozenset(cfg.symbols), cfg.numbers_as_digits, frozenset(cfg.structural))


@pytest.mark.parametrize(
    "spoken",
    [
        "Das ist größer als gedacht.",
        "Es ist kleiner als erwartet.",
        "That is less than I thought.",
        "Profit is greater than cost.",
        "open a new tab please",
        "Öffne einen neuen Tabulator bitte.",
    ],
)
def test_ambiguous_commands_are_off_in_the_default_config(spoken):
    assert _with_default_config(spoken) == spoken


def test_default_config_still_recognises_the_safe_commands():
    assert _with_default_config("path slash file new line next") == "path/file\nnext"


def test_less_than_and_tab_still_work_when_enabled():
    assert process_voice_commands("a less than b", frozenset({"less than"})) == "a<b"
    assert process_voice_commands("a größer als b", frozenset({"greater than"})) == "a>b"
    assert process_voice_commands("a tab b", active_structural=frozenset({"tab"})) == "a\tb"


# --- Punctuation the speech model puts after a command ---


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Hello. New line. World.", "Hello.\nWorld."),
        ("Hello. New paragraph. World.", "Hello.\n\nWorld."),
        ("Hallo. Neue Zeile. Welt.", "Hallo.\nWelt."),
        ("Hello, new line, world", "Hello\nworld"),
        ("Hello. New line.", "Hello.\n"),
        ("Hello. New line... World.", "Hello.\nWorld."),
        ("Save as new line .gitignore", "Save as\n.gitignore"),
    ],
)
def test_punctuation_after_a_command_is_dropped(spoken, expected):
    assert process_voice_commands(spoken) == expected


def test_punctuation_after_a_symbol_command_is_dropped():
    assert process_voice_commands("path slash. file", frozenset({"slash"})) == "path/file"


# --- "all caps" inside an ordinary sentence ---


@pytest.mark.parametrize(
    "spoken",
    [
        "I wrote it in all caps today",
        "He wrote in all caps.",
        "Please do not use all caps",
        "Is all caps rude?",
        "Er hat alles groß geschrieben",
        "Schreib das in alles groß.",
        "all caps",
    ],
)
def test_all_caps_in_a_normal_sentence_is_typed_as_text(spoken):
    assert process_voice_commands(spoken) == spoken


def test_all_caps_still_works_as_a_command():
    assert process_voice_commands("Hello. All caps wow, new line next") == "Hello. WOW\nnext"
    assert process_voice_commands("alles groß achtung") == "ACHTUNG"
    assert process_voice_commands("all caps hello new line all caps world") == "HELLO\nWORLD"
