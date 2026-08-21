"""Tests for voice command processing."""

from __future__ import annotations

from nexusvox.voice_commands import process_voice_commands

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
