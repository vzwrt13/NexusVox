"""The dashboard status colour rule: green ok, yellow warn, red down, all through one helper."""

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
_CSS = (_STATIC / "style.css").read_text(encoding="utf-8")
_JS = (_STATIC / "dashboard.js").read_text(encoding="utf-8")
_HTML = (_STATIC / "index.html").read_text(encoding="utf-8")


def test_each_state_has_its_own_colour():
    colours = {}
    for state in ("ok", "warn", "down"):
        match = re.search(rf"\.status-line\.{state}\s*\{{\s*color:\s*var\((--[a-z]+)\)", _CSS)
        assert match, f".status-line.{state} is missing"
        colours[state] = match.group(1)
    assert colours == {"ok": "--green", "warn": "--yellow", "down": "--red"}


def test_the_yellow_token_is_defined_on_root():
    assert re.search(r":root\s*\{[^}]*--yellow:\s*#[0-9a-fA-F]{6}", _CSS)


def test_the_helper_accepts_exactly_the_three_states():
    assert 'const STATUS_STATES = ["ok", "warn", "down"];' in _JS
    assert "function setStatus(el, state, text)" in _JS


def test_status_lines_are_not_styled_by_hand():
    # Only the helper may write a status-line class name; the one other writer is the rename error note.
    assert len(re.findall(r'className = "status-line', _JS)) <= 2
    assert "className = \"status-line ok\"" not in _JS


def test_caveat_hints_use_the_warn_state():
    assert re.search(r'id="cleanup-cpu-hint"', _HTML)
    assert re.search(r'class="[^"]*status-line warn[^"]*"\s+id="cleanup-cpu-hint"', _HTML)
    assert re.search(r'class="[^"]*model-caution[^"]*status-line warn', _HTML)
    assert ".badge.warn" in _CSS
