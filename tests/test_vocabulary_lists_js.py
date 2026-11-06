"""The Vocabulary list helpers of the dashboard scripts (count, filter, collapse state), run under Node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_js import dashboard_source

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (_STATIC / "index.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

FAKE = """
const store = {};
const localStorage = {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
const nodes = {};
function node(id) {
  return nodes[id] || (nodes[id] = {id, value: '', hidden: false, textContent: '', innerHTML: '', scrollTop: 0,
    attrs: {}, on: {}, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { this.on[t] = f; }});
}
const document = {getElementById: node};
const escapeHtml = (s) => String(s);
let dictCache = [], presetCache = [];
const row = (e) => `<r>${e.wrong}</r>`;
const FIELDS = ['wrong', 'right'];
function entries(n) {
  return Array.from({length: n}, (_, i) => ({id: i, wrong: 'spoken ' + i, right: 'Written ' + i}));
}
"""


def run_js(body: str):
    source = dashboard_source()
    start = source.index("// ── Vocabulary lists")
    helpers = source[start : source.index("// ── end of Vocabulary lists")]
    helpers += source[source.index("function renderDictionary") : source.index("async function addDictionaryEntry")]
    helpers += source[source.index("function renderPolishPresets") : source.index("function resetPresetForm")]
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


def test_german_filter_folds_sharp_s_case_and_decomposed_umlauts():
    out = run_js(
        "const items = [{wrong: 'strasse', right: 'Stra\u00dfe'}, {wrong: 'u', right: 'gr\u00fc\u00dfen'}];"
        "const hits = (q) => vocabFilter(items, q, FIELDS).length;"
        "console.log(JSON.stringify([hits('STRASSE'), hits('Stra\u00dfe'), hits('gru\u0308\u00dfen'),"
        " hits('gr\u00fcssen'), hits('zzz')]));"
    )
    assert out == [1, 1, 1, 1, 0]


def test_500_entries_render_only_the_matching_rows():
    out = run_js(
        "dictCache = entries(500); node('dict-filter').value = 'spoken 49'; renderDictionary();"
        "const some = node('dict-list').innerHTML.split('dict-row').length - 1;"
        "node('dict-filter').value = ''; renderDictionary();"
        "console.log(JSON.stringify([some, node('dict-list').innerHTML.split('dict-row').length - 1]));"
    )
    assert out == [11, 500]


def test_collapsed_list_builds_no_rows_and_expanding_renders_them():
    out = run_js(
        "vocabSetCollapsed('dict', true); dictCache = entries(5); wireVocabBox('dict', renderDictionary);"
        "renderDictionary(); const collapsed = node('dict-list').innerHTML;"
        "node('dict-toggle').on.click(); const open = node('dict-list').innerHTML.includes('dict-row');"
        "console.log(JSON.stringify([collapsed, open, node('dict-body').hidden]));"
    )
    assert out == ["", True, False]


def test_collapsing_clears_the_filter_and_typing_scrolls_to_the_top():
    out = run_js(
        "dictCache = entries(30); wireVocabBox('dict', renderDictionary);"
        "node('dict-list').scrollTop = 500; node('dict-filter').value = 'spoken 1';"
        "node('dict-filter').on.input(); const top = node('dict-list').scrollTop;"
        "node('dict-toggle').on.click();"
        "console.log(JSON.stringify([top, node('dict-filter').value, node('dict-toggle').textContent]));"
    )
    assert out[0] == 0 and out[1] == "" and out[2].endswith("Dictionary (30)")


def test_toggle_shows_its_title_before_the_list_has_loaded():
    out = run_js(
        "wireVocabBox('preset', renderPolishPresets); console.log(JSON.stringify(node('preset-toggle').textContent));"
    )
    assert out.endswith("Presets")


def test_reveal_clears_filter_expands_and_resets_the_scroll():
    out = run_js(
        "vocabSetCollapsed('dict', true); node('dict-filter').value = 'nexus'; node('dict-list').scrollTop = 400;"
        "vocabReveal('dict');"
        "const l = node('dict-list');"
        "console.log(JSON.stringify([node('dict-filter').value, vocabIsCollapsed('dict'), l.scrollTop]));"
    )
    assert out == ["", False, 0]


def test_scroll_to_moves_the_saved_row_into_view():
    out = run_js(
        "const l = node('dict-list'); l.scrollTop = 0;"
        "l.getBoundingClientRect = () => ({top: 100});"
        "l.querySelector = (q) => (q === '[data-id=\"7\"]' ? {getBoundingClientRect: () => ({top: 460})} : null);"
        "vocabScrollTo('dict', 7); vocabScrollTo('dict', 8); console.log(JSON.stringify(l.scrollTop));"
    )
    assert out == 360


def test_preset_rows_show_name_default_marker_and_instruction():
    out = run_js(
        "presetCache = [{id: 3, name: 'formal', instruction: 'Be <formal>', is_default: true}];"
        "renderPolishPresets(); console.log(JSON.stringify(node('preset-list').innerHTML));"
    )
    assert 'data-id="3"' in out and "formal (default)" in out and "Be <formal>" in out


def test_markup_has_a_box_per_list_and_scripts_wire_them():
    for key in ("dict", "preset"):
        for suffix in ("toggle", "body", "filter", "list"):
            assert f'id="{key}-{suffix}"' in HTML
    assert re.search(r'wireVocabBox\("dict"', dashboard_source())
    assert re.search(r'wireVocabBox\("preset"', dashboard_source())
