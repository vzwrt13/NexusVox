"""Tests for spoken snippet triggers."""

from __future__ import annotations

import pytest

from nexusvox.snippets import (
    Snippet,
    expand_snippets,
    mark_snippets,
    parse_options,
    placeholders_intact,
    resolve_nexus_open,
)
from nexusvox.voice_commands import process_voice_commands

ADDRESS = "Jane Doe\nMain Street 1\n12345 Springfield"
SIGNATURE = "Viele Grüße\nVincent"


def _run(text: str, snippets: list[Snippet]) -> str:
    marked, expansions = mark_snippets(text, snippets)
    return expand_snippets(marked, expansions)


def test_trigger_anywhere_in_the_dictation():
    assert _run("Please send it to my address today", [Snippet("my address", ADDRESS)]) == (
        f"Please send it to {ADDRESS} today"
    )


def test_german_trigger():
    assert _run("Danke dir. Meine Signatur", [Snippet("meine Signatur", SIGNATURE)]) == f"Danke dir. {SIGNATURE}"


def test_trigger_alone_drops_the_sentence_mark():
    assert _run("My address.", [Snippet("my address", ADDRESS)]) == ADDRESS
    assert _run("Meine Signatur!", [Snippet("meine Signatur", SIGNATURE)]) == SIGNATURE


def test_case_and_whitespace_insensitive():
    assert _run("MY   Address", [Snippet("my address", ADDRESS)]) == ADDRESS


def test_whole_words_only():
    snippets = [Snippet("sig", SIGNATURE)]
    assert _run("the signal is strong", snippets) == "the signal is strong"


def test_longest_trigger_wins():
    snippets = [Snippet("my address", ADDRESS), Snippet("my work address", "Office 7")]
    assert _run("ship it to my work address", snippets) == "ship it to Office 7"


def test_trigger_used_twice():
    assert _run("sig and sig", [Snippet("sig", "S")]) == "S and S"


def test_no_snippets_leaves_text_alone():
    assert mark_snippets("my address", []) == ("my address", [])


def test_voice_commands_do_not_touch_the_expansion():
    snippets = [Snippet("my template", "tab new line all caps")]
    marked, expansions = mark_snippets("intro new line my template", snippets)
    processed = process_voice_commands(marked)
    assert expand_snippets(processed, expansions) == "intro\ntab new line all caps"


def test_placeholders_intact():
    marked, expansions = mark_snippets("Hallo meine Signatur", [Snippet("meine Signatur", SIGNATURE)])
    assert placeholders_intact(marked, expansions)
    assert not placeholders_intact("Hello", expansions)
    assert placeholders_intact("anything", [])


# --- Options ---

REPOS = (
    ("NexusVox", "https://github.com/vzwrt13/NexusVox"),
    ("nexusvox-site", "https://github.com/vzwrt13/nexusvox-site"),
    ("personal-tooling", "https://github.com/vzwrt13/personal-tooling"),
    ("sprechblase", "https://github.com/vzwrt13/sprechblase"),
)
REPO_SNIPPET = Snippet("open remote repo", "", REPOS)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Open remote repo Nexus Box.", "https://github.com/vzwrt13/NexusVox"),
        ("open remote repo, personal tooling", "https://github.com/vzwrt13/personal-tooling"),
        ("open remote repo nexus vox site", "https://github.com/vzwrt13/nexusvox-site"),
        ("Bitte open remote repo Sprech Blase und dann", "Bitte https://github.com/vzwrt13/sprechblase und dann"),
    ],
)
def test_option_best_match(spoken, expected):
    assert _run(spoken, [REPO_SNIPPET]) == expected


def test_option_without_match_keeps_the_text():
    assert _run("open remote repo banana", [REPO_SNIPPET]) == "open remote repo banana"
    assert _run("open remote repo", [REPO_SNIPPET]) == "open remote repo"


def test_option_without_match_uses_the_fallback_text():
    snippet = Snippet("meine Adresse", "Hauptstraße 1", (("Büro", "Office 7"),))
    assert _run("meine Adresse Büro", [snippet]) == "Office 7"
    assert _run("meine Adresse.", [snippet]) == "Hauptstraße 1"


def test_parse_options():
    assert parse_options("NexusVox = https://a\n\n bad line \nname=  x = y \n = empty") == (
        ("NexusVox", "https://a"),
        ("name", "x = y"),
    )


# --- nexus open ---


@pytest.mark.parametrize(
    "spoken",
    [
        "Nexus open remote repo Nexus Box.",
        "nexus, öffne Remote Repo Nexus Vox",
        "Nexus öffnen remote repo nexusvox",
    ],
)
def test_nexus_open_resolves_the_option(spoken):
    assert resolve_nexus_open(spoken, [REPO_SNIPPET]) == "https://github.com/vzwrt13/NexusVox"


def test_nexus_open_plain_snippet_both_languages():
    snippets = [Snippet("meine Webseite", "https://nexusvox.nightshift-ai.de")]
    assert resolve_nexus_open("nexus öffne meine Webseite", snippets) == "https://nexusvox.nightshift-ai.de"
    assert resolve_nexus_open("nexus open meine Webseite.", snippets) == "https://nexusvox.nightshift-ai.de"


@pytest.mark.parametrize(
    "spoken",
    [
        "nexus remote repo nexusvox",  # no verb
        "nexus open chrome",  # an app, not a snippet
        "nexus open remote repo banana",  # no option matches
        "open remote repo nexusvox",  # no nexus: that types the URL instead
        "nexus open remote repo nexusvox and more",  # not the whole phrase
    ],
)
def test_nexus_open_not_resolved(spoken):
    assert resolve_nexus_open(spoken, [REPO_SNIPPET]) is None
