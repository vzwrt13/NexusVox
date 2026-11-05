"""The Vocabulary list helpers of dashboard.js (count, filter, collapse state), run under Node."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
JS = _STATIC / "dashboard.js"
HTML = (_STATIC / "index.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

FAKE = """
const store = {};
const localStorage = {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
const nodes = {};
function node(id) {
  return nodes[id] || (nodes[id] = {id, value: '', hidden: false, textContent: '', innerHTML: '',
    attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }});
}
const document = {getElementById: node};
const escapeHtml = (s) => String(s);
const row = (e) => `<r>${e.wrong}</r>`;
const FIELDS = ['wrong', 'right'];
function entries(n) {
  return Array.from({length: n}, (_, i) => ({id: i, wrong: 'spoken ' + i, right: 'Written ' + i}));
}
"""


def run_js(body: str):
    source = JS.read_text(encoding="utf-8")
    start = source.index("// ── Vocabulary lists")
    helpers = source[start : source.index("// ── end of Vocabulary lists")]
    out = subprocess.run(
        ["node", "-e", f"{FAKE}\n{helpers}\n{body}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    ).stdout
    return json.loads(out)


def test_header_shows_the_entry_count():
    out = run_js(
        "vocabRender('dict', entries(124), FIELDS, 'none', row);"
        "console.log(JSON.stringify(node('dict-toggle').textContent));"
    )
    assert out.endswith("Dictionary (124)")


def test_filter_matches_spoken_and_written_side_and_shows_the_shown_count():
    out = run_js(
        "const items = [{wrong: 'nexus fox', right: 'NexusVox'}, {wrong: 'foo', right: 'Bar'}];"
        "node('dict-filter').value = 'NEXUSV'; vocabRender('dict', items, FIELDS, 'none', row);"
        "const a = node('dict-list').innerHTML + node('dict-toggle').textContent;"
        "node('dict-filter').value = 'fox'; vocabRender('dict', items, FIELDS, 'none', row);"
        "const b = node('dict-list').innerHTML;"
        "node('dict-filter').value = 'zzz'; vocabRender('dict', items, FIELDS, 'none', row);"
        "console.log(JSON.stringify([a, b, node('dict-list').innerHTML]));"
    )
    assert "<r>nexus fox</r>" in out[0] and "foo" not in out[0] and "(1 of 2)" in out[0]
    assert out[1] == "<r>nexus fox</r>"
    assert "No matches" in out[2]


def test_empty_list_shows_the_empty_text():
    out = run_js(
        "vocabRender('preset', [], ['name'], 'No presets yet.', row);"
        "console.log(JSON.stringify(node('preset-list').innerHTML));"
    )
    assert "No presets yet." in out


def test_collapse_state_is_remembered_per_list():
    out = run_js(
        "vocabSetCollapsed('dict', true);"
        "vocabRender('dict', entries(3), FIELDS, 'none', row); vocabRender('preset', entries(3), FIELDS, 'none', row);"
        "console.log(JSON.stringify([node('dict-body').hidden, node('preset-body').hidden,"
        " node('dict-toggle').attrs['aria-expanded'], vocabIsCollapsed('dict'), vocabIsCollapsed('preset')]));"
    )
    assert out == [True, False, "false", True, False]


def test_500_entries_filter_quickly():
    start = time.perf_counter()
    out = run_js(
        "const items = entries(500); node('dict-filter').value = 'spoken 49';"
        "const t = Date.now(); for (let i = 0; i < 20; i++) vocabRender('dict', items, FIELDS, 'none', row);"
        "console.log(JSON.stringify([Date.now() - t, node('dict-toggle').textContent]));"
    )
    assert out[0] < 500 and "(11 of 500)" in out[1]
    assert time.perf_counter() - start < 10


def test_markup_has_a_box_per_list_and_scripts_wire_them():
    for key in ("dict", "preset"):
        for suffix in ("toggle", "body", "filter", "list"):
            assert f'id="{key}-{suffix}"' in HTML
    assert re.search(r'wireVocabBox\("dict"', JS.read_text(encoding="utf-8"))
    assert re.search(r'wireVocabBox\("preset"', JS.read_text(encoding="utf-8"))
