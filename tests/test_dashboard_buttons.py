"""The dashboard's shared control set: no native (white) button or field is left, every action button is `.btn`."""

import re
from pathlib import Path

import pytest
from dashboard_js import dashboard_source

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
_CSS = "".join((_STATIC / name).read_text(encoding="utf-8") for name in ("style.css", "setup.css", "recordings.css"))
_HTML = (_STATIC / "index.html").read_text(encoding="utf-8")
_JS = re.sub(r"(?m)^\s*//.*$", "", dashboard_source())  # comments may name a tag
_SOURCES = {"index.html": _HTML, "scripts": _JS}

_KINDS = {"btn-primary", "btn-tint", "btn-ghost"}
_FIELD_CLASSES = {"dict-input", "select-input", "date-input", "flagged-textarea", "upload-rename-input"}
# Input types that are not a text field: they have their own native look, set dark through `color-scheme`.
_NOT_TEXT_FIELDS = {"checkbox", "radio", "file", "hidden", "submit", "button", "range", "progress"}


def _has_rule(token: str) -> bool:
    return re.search(rf"\.{re.escape(token)}(?![\w-])", _CSS) is not None


def _classes(tag: str) -> list[str]:
    match = re.search(r'class=\\?"([^"\\]*)', tag)
    return match.group(1).split() if match else []


def _tags(source: str, name: str) -> list[str]:
    return re.findall(rf"<{name}\b[^>]*>", source)


def test_the_page_is_dark_for_native_widgets():
    assert re.search(r":root\s*\{[^}]*color-scheme:\s*dark", _CSS)


def test_the_button_kinds_and_size_exist_with_a_disabled_and_focus_state():
    for token in (*_KINDS, "btn", "btn-sm"):
        assert _has_rule(token), token
    assert ".btn:focus-visible" in _CSS and ".btn:disabled" in _CSS


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_every_button_has_a_class_with_a_style_rule(name):
    bare = []
    for tag in _tags(_SOURCES[name], "button"):
        if not any(_has_rule(token) for token in _classes(tag)):
            bare.append(tag)
    assert not bare, f"native, unstyled buttons in {name}: {bare}"


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_a_btn_has_exactly_one_kind(name):
    wrong = []
    for tag in _tags(_SOURCES[name], "button") + _tags(_SOURCES[name], "a"):
        tokens = _classes(tag)
        if "btn" in tokens and len(_KINDS & set(tokens)) != 1:
            wrong.append(tag)
    assert not wrong, f"a .btn needs one of {sorted(_KINDS)}: {wrong}"


def test_a_button_made_in_script_gets_a_styled_class():
    bare = []
    for match in re.finditer(r'createElement\("(button|a)"\)', _JS):
        window = _JS[match.end() : match.end() + 400]
        classes = re.search(r'\.className\s*=\s*"([^"]*)"', window)
        if match.group(1) == "a" and not (classes and "btn" in classes.group(1).split()):
            continue  # a plain text link is not a button
        if not classes or not any(_has_rule(token) for token in classes.group(1).split()):
            bare.append(window.splitlines()[0] if window else match.group(0))
        elif "btn" in classes.group(1).split() and len(_KINDS & set(classes.group(1).split())) != 1:
            bare.append(f"{classes.group(1)!r} needs exactly one of {sorted(_KINDS)}")
    assert not bare, f"buttons made in script without a styled class or a kind: {bare}"


def test_a_field_made_in_script_gets_the_dark_field_class():
    bare = []
    for match in re.finditer(r'createElement\("(input|select|textarea)"\)', _JS):
        window = _JS[match.end() : match.end() + 400]
        classes = re.search(r'\.className\s*=\s*"([^"]*)"', window)
        kind = re.search(r'\.type\s*=\s*"(\w+)"', window)
        if kind and kind.group(1) in _NOT_TEXT_FIELDS:
            continue
        if not classes or not _FIELD_CLASSES & set(classes.group(1).split()):
            bare.append(match.group(0) + " " + window.splitlines()[0])
    assert not bare, f"fields made in script without the dark field class: {bare}"


def test_at_most_one_primary_button_per_page_or_dialog():
    # A page is a tab, a dialog or a first-run setup panel (the wizard shows one panel at a time). A dialog sits
    # inside its tab, so it is counted on its own and left out of the tab.
    blocks = re.findall(r"<dialog\b.*?</dialog>", _HTML, re.S)
    page_html = re.sub(r"<dialog\b.*?</dialog>", "", _HTML, flags=re.S)
    blocks += re.split(r'<section class="setup-panel', page_html)[1:]
    main = page_html.split("</main>")[0]  # the setup overlay after it is not part of the last tab
    blocks += re.split(r'<section id="tab-[\w-]+" class="tab-content', main)[1:]
    assert len(blocks) >= 10
    for block in blocks:
        assert len(re.findall(r"\bbtn-primary\b", block)) <= 1, block[:120]


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_every_text_field_has_the_dark_field_class(name):
    bare = []
    for tag in _tags(_SOURCES[name], "input"):
        kind = re.search(r'type="(\w+)"', tag)
        if kind and kind.group(1) in _NOT_TEXT_FIELDS:
            continue
        if not _FIELD_CLASSES & set(_classes(tag)):
            bare.append(tag)
    for tag in _tags(_SOURCES[name], "select") + _tags(_SOURCES[name], "textarea"):
        if not _FIELD_CLASSES & set(_classes(tag)):
            bare.append(tag)
    # The Recordings search box is styled through its wrapper (`.rec-search input`).
    bare = [tag for tag in bare if 'id="rec-search"' not in tag]
    assert not bare, f"native, unstyled fields in {name}: {bare}"


def test_fields_have_a_dark_ground_and_a_violet_focus():
    for selector in (".dict-input", ".select-input", ".date-input", ".flagged-textarea", ".upload-rename-input"):
        block = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", _CSS).group(1)
        assert re.search(r"background(-color)?:\s*var\(--bg\)", block), selector
        assert re.search(re.escape(selector) + r":focus\s*\{[^}]*border-color:\s*var\(--accent\)", _CSS), selector


def test_hidden_buttons_and_status_rows_stay_hidden():
    # `display` on the class would beat the `hidden` attribute (Download, Not now, a hidden status row).
    assert ".btn[hidden]" in _CSS and ".status-row[hidden]" in _CSS


def test_a_delete_button_turns_red_on_hover():
    # `.btn-ghost:hover` is more specific than `.upload-delete-btn:hover`, so the danger rule has to outrank it.
    assert ".btn.upload-delete-btn:hover:not(:disabled) { border-color: var(--red); color: var(--red); }" in _CSS


def test_the_new_colours_are_tokens():
    root = re.search(r":root\s*\{([^}]*)\}", _CSS).group(1)
    for token in ("--on-accent", "--surface-hover", "--yellow-soft", "--red-soft"):
        assert token + ":" in root, token
    block = _CSS[_CSS.index("/* Shared controls.") : _CSS.index("/* Sidebar:")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba\(", block), "raw colours in the shared button rules"
