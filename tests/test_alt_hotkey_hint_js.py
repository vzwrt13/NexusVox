"""The yellow Alt hint under the dictation and meeting hotkeys, run under Node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_js import dashboard_source

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (_STATIC / "index.html").read_text(encoding="utf-8")
TEXT = "Alt in a hotkey can open the menu bar in apps such as Notepad. Pasted text can then get lost."

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def run_js(body: str):
    source = dashboard_source()
    status = re.search(r"const STATUS_STATES.*?\n\}\n", source, re.S).group(0)
    hint = source[source.index("// ── Alt hotkey hint") : source.index("// ── end of Alt hotkey hint")]
    fake = (
        "const mk = () => ({hidden: true, textContent: '', _c: new Set(),"
        " classList: {add(c){this._s.add(c)}, remove(...c){c.forEach(x=>this._s.delete(x))}, _s: null}});\n"
        "const el = () => { const e = mk(); e.classList._s = e._c; return e; };\n"
    )
    out = subprocess.run(
        ["node", "-e", f"{fake}{status}\n{hint}\n{body}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    ).stdout
    return json.loads(out)


@pytest.mark.parametrize("hotkey", ["hotkey", "meeting"])
def test_hint_element_exists_yellow_for_each_hotkey(hotkey):
    assert re.search(rf'id="{hotkey}(-hotkey)?-alt-hint"\s+class="status-line warn"', HTML)


def test_hint_shows_with_alt_and_clears_without():
    out = run_js(
        """
        const e = el();
        renderAltHint(e, ["ctrl", "alt"]);
        const shown = [e.hidden, e.textContent, e._c.has("warn")];
        renderAltHint(e, ["ctrl", "win"]);
        const cleared = [e.hidden, e.textContent, e._c.has("warn")];
        console.log(JSON.stringify([shown, cleared]));"""
    )
    assert out[0] == [False, TEXT, True]
    assert out[1] == [True, "", False]


def test_both_hotkey_renderers_call_the_hint():
    source = dashboard_source()
    assert "renderAltHint(hotkeyAltHint, s.hotkey_modifiers)" in source
    assert "renderAltHint(meetingAltHint, s.meeting_modifiers)" in source
