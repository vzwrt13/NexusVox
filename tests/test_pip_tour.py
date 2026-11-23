"""Pip's what's-new popup on Home: the data file, once per version, no entries = no popup, a first installation, and the
interplay with the daily bubble, run under Node with stand-ins for the page."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import STATIC

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

TEXTS = (STATIC / "dashboard-pip-texts.js").read_text(encoding="utf-8")
GREETING = (STATIC / "dashboard-pip-greeting.js").read_text(encoding="utf-8")
DATA = (STATIC / "dashboard-pip-whats-new.js").read_text(encoding="utf-8")
TOUR = (STATIC / "dashboard-pip-tour.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")

# Stand-ins for the page: a localStorage, Pip's mount recorded per call, a minimal document and the invented entries.
PAGE = r"""
const store = {};
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
let pipEnabled = true;
const mounts = [];
function mountPip(host, options) {
  mounts.push({ pose: options.pose, side: options.side, bubble: options.bubble || null });
  return host;
}
function localIsoDate() { return "2026-11-02"; }
const document = { createElement: (tag) => ({ tag, children: [], className: "", textContent: "", listeners: {},
  attrs: {}, setAttribute(k, v) { this.attrs[k] = v; },
  append(...k) { this.children.push(...k); }, addEventListener(t, f) { this.listeners[t] = f; } }) };
const SAMPLE = {
  "1.1.0": [
    { title: "Spoken formatting", text: "Say \"new paragraph\" while you dictate.", target: "settings" },
    { title: "Own words", text: "Add names under Vocabulary." },
    { title: "Meetings", text: "Record both sides.", target: "recordings" },
  ],
  "1.2.0": [],
};
const USED = { words: 900, dictations: 12, meetings: 1, uploads: 1, dictionary_entries: 1, formatting_commands: 1 };
const NEW_USER = { words: 0, dictations: 0, meetings: 0, uploads: 0, dictionary_entries: 0, formatting_commands: 0 };
const last = () => mounts[mounts.length - 1];
const find = (node, cls) => (node.className.split(" ").includes(cls) ? node
  : node.children.map((c) => (c && c.children ? find(c, cls) : null)).find(Boolean) || null);
const read = (k) => JSON.parse(store[k]);
"""


def _js(body: str):
    program = f"{TEXTS}\n{GREETING}\n{PAGE}\n{DATA}\n{TOUR}\n{body}"
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── The data file ────────────────────────────────────────────────────


def test_the_page_loads_the_data_before_the_logic_and_after_the_greeting():
    order = [m for m in re.findall(r'src="(dashboard-pip[^"]*\.js)"', HTML)]
    assert order == [
        "dashboard-pip.js",
        "dashboard-pip-texts.js",
        "dashboard-pip-greeting.js",
        "dashboard-pip-whats-new.js",
        "dashboard-pip-tour.js",
    ]


@needs_node
def test_every_entry_is_short_english_with_a_real_target_and_never_mentions_a_paid_plan():
    shipped = _js("console.log(JSON.stringify(PIP_WHATS_NEW))")
    tabs = set(re.findall(r'data-tab="([a-z-]+)"', HTML))
    for version, entries in shipped.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), "the key is the package version, without a v"
        assert 1 <= len(entries) <= 5, version
        for entry in entries:
            assert set(entry) <= {"title", "text", "target"}, entry
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", entry["text"].strip()) if s]
            assert 1 <= len(sentences) <= 2 and len(entry["text"]) <= 170, entry
            assert 0 < len(entry["title"]) <= 40, entry
            assert not re.search(r"\b(pro|premium|upgrade|subscription|licen[cs]e|paid|price)\b", entry["text"], re.I)
            if "target" in entry:
                assert entry["target"].split("/")[0] in tabs, entry


def test_the_data_and_the_logic_need_no_network():
    for source in (DATA, TOUR):
        assert not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|https?://)", source)


# ── Once per version, no entries, first installation ─────────────────


@needs_node
def test_the_popup_shows_once_per_version_and_never_again_for_it():
    got = _js(
        "const a = pickPipTour('1.1.0', USED, emptyPipTourState(), '2026-11-02', SAMPLE);"
        "const b = pickPipTour('1.1.0', USED, a.state, '2026-11-03', SAMPLE);"
        "const c = pickPipTour('v1.1.0', USED, a.state, '2026-11-03', SAMPLE);"
        "console.log(JSON.stringify({ a: a.steps.map((s) => s.title), state: a.state, b: b.steps, c: c.steps }))"
    )
    assert got["a"] == ["Spoken formatting", "Own words", "Meetings"]
    assert got["state"] == {"version": "1.1.0", "day": "2026-11-02"}
    assert got["b"] == [] and got["c"] == []


@needs_node
def test_a_later_version_with_entries_shows_again():
    got = _js(
        "const data = { ...SAMPLE, '1.3.0': [{ title: 'New', text: 'Something new.' }] };"
        "const a = pickPipTour('1.1.0', USED, emptyPipTourState(), '2026-11-02', data);"
        "const b = pickPipTour('1.3.0', USED, a.state, '2026-12-01', data);"
        "console.log(JSON.stringify([b.steps.map((s) => s.title), b.state]))"
    )
    assert got == [["New"], {"version": "1.3.0", "day": "2026-12-01"}]


@needs_node
def test_a_version_without_entries_shows_nothing_and_is_remembered_quietly():
    got = _js(
        "const none = pickPipTour('1.0.0', USED, emptyPipTourState(), '2026-11-02', SAMPLE);"
        "const empty = pickPipTour('1.2.0', USED, emptyPipTourState(), '2026-11-02', SAMPLE);"
        "const bad = pickPipTour('1.1.0', USED, emptyPipTourState(), '2026-11-02',"
        " { '1.1.0': [{ title: '', text: 'x' }, { text: 'no title' }, null, 'x'] });"
        "const shipped = pickPipTour('1.0.0', USED, emptyPipTourState(), '2026-11-02');"
        "console.log(JSON.stringify([none.steps, none.state, empty.steps, bad.steps, shipped.steps]))"
    )
    assert got == [[], {"version": "1.0.0", "day": ""}, [], [], []]


@needs_node
def test_a_first_installation_is_not_an_update_but_an_existing_history_is():
    got = _js(
        "const fresh = pickPipTour('1.1.0', NEW_USER, emptyPipTourState(), '2026-11-02', SAMPLE);"
        "const freshLater = pickPipTour('1.1.0', USED, fresh.state, '2026-11-09', SAMPLE);"
        "const updated = pickPipTour('1.1.0', { ...NEW_USER, uploads: 1 }, emptyPipTourState(), '2026-11-02', SAMPLE);"
        "console.log(JSON.stringify([fresh.steps, fresh.state, freshLater.steps, updated.steps.length]))"
    )
    assert got == [[], {"version": "1.1.0", "day": ""}, [], 3]


@needs_node
def test_without_a_version_or_the_counts_nothing_is_decided_or_used_up():
    got = _js(
        "const state = emptyPipTourState();"
        "const noVersion = pickPipTour('', USED, state, '2026-11-02', SAMPLE);"
        "const noFacts = pickPipTour('1.1.0', null, state, '2026-11-02', SAMPLE);"
        "console.log(JSON.stringify("
        "[noVersion.steps, noFacts.steps, noVersion.state === state, noFacts.state === state]))"
    )
    assert got == [[], [], True, True]


# ── On the page ──────────────────────────────────────────────────────


@needs_node
def test_the_steps_walk_with_next_and_done_and_closing_ends_the_popup_for_good():
    got = _js(
        "const host = {};"
        "const kind = showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE);"
        "const one = last();"
        "const eyebrow1 = find(one.bubble, 'pip-bubble-eyebrow').textContent;"
        "const title1 = find(one.bubble, 'pip-tour-title').textContent;"
        "find(one.bubble, 'pip-tour-next').listeners.click();"
        "const two = last();"
        "const label2 = find(two.bubble, 'pip-tour-next').textContent;"
        "const show2 = find(two.bubble, 'pip-tour-show');"
        "find(two.bubble, 'pip-tour-next').listeners.click();"
        "const three = last();"
        "const labelLast = find(three.bubble, 'pip-tour-next').textContent;"
        "find(three.bubble, 'pip-tour-next').listeners.click();"  # Done
        "const afterDone = last();"
        "const again = showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE);"
        "console.log(JSON.stringify({ kind, eyebrow1, title1, label2, show2: !!show2, labelLast,"
        " afterDone: [afterDone.pose, afterDone.bubble], again, saved: read('nexusvox-pip-whats-new') }))"
    )
    assert got["kind"] == "tour"
    assert got["eyebrow1"] == "What's new · 1 of 3" and got["title1"] == "Spoken formatting"
    assert got["label2"] == "Next" and got["show2"] is False  # the second entry has no target
    assert got["labelLast"] == "Done"
    assert got["afterDone"] == ["idle", None]
    assert got["again"] == "quiet"  # the same day, Home opened again: no popup and no bubble
    assert got["saved"] == {"version": "1.1.0", "day": "2026-11-02"}


@needs_node
def test_the_x_closes_at_any_time_and_has_an_accessible_name():
    got = _js(
        "const host = {}; showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE);"
        "const close = find(last().bubble, 'pip-bubble-close');"
        "const meta = [close.textContent, close.attrs, close.type];"
        "close.listeners.click();"
        "console.log(JSON.stringify([meta, last().bubble, showPipHome(host, USED, '1.1.0', '2026-11-03', SAMPLE)]))"
    )
    assert got[0] == ["×", {"aria-label": "Close"}, "button"]
    assert got[1] is None
    assert got[2] != "tour"  # never again for this version


@needs_node
def test_show_me_opens_the_target_and_ends_the_popup():
    got = _js(
        "const opened = []; const host = {};"
        "showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE, (t) => opened.push(t));"
        "find(last().bubble, 'pip-tour-show').listeners.click();"
        "console.log(JSON.stringify([opened, last().bubble]))"
    )
    assert got == [["settings"], None]


@needs_node
def test_the_popup_replaces_the_daily_bubble_that_day_and_the_bubble_is_not_used_up():
    got = _js(
        "const host = {};"
        "const first = showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE);"
        "const popupMount = last();"
        # untouched below: nothing said, nothing used up
        "const greetingAfterPopup = localStorage.getItem('nexusvox-pip-greeting');"
        "const sameDay = showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE);"
        "const sameDayMount = last();"
        "const nextDay = showPipHome(host, USED, '1.1.0', '2026-11-03', SAMPLE);"
        "const nextMount = last();"
        "console.log(JSON.stringify({ first, hasBubble: !!popupMount.bubble, greetingAfterPopup, sameDay,"
        " sameDayBubble: sameDayMount.bubble, nextDay, nextBubble: !!nextMount.bubble,"
        " said: read('nexusvox-pip-greeting').id }))"
    )
    assert got["first"] == "tour" and got["hasBubble"] is True
    assert got["greetingAfterPopup"] is None  # the day's bubble was not chosen, so nothing is used up
    assert got["sameDay"] == "quiet" and got["sameDayBubble"] is None  # not even a bubble after the popup is closed
    assert got["nextDay"] == "greeting" and got["nextBubble"] is True  # the next day Pip greets as usual
    assert got["said"]


@needs_node
def test_without_a_popup_due_the_daily_bubble_works_as_before():
    got = _js(
        "const host = {};"
        "const a = showPipHome(host, USED, '1.0.0', '2026-11-02', SAMPLE);"
        "const b = showPipHome(host, USED, '1.0.0', '2026-11-02', SAMPLE);"
        "const c = showPipHome(host, null, '1.0.0', '2026-11-02', SAMPLE);"
        "console.log(JSON.stringify([a, b, c, last().bubble]))"
    )
    assert got[:3] == ["greeting", "greeting", "quiet"]  # the day keeps its bubble; no counts: Pip sits quietly
    assert got[3] is None


@needs_node
def test_a_popup_that_could_not_be_shown_is_not_used_up():
    got = _js(
        "const host = {};"
        "pipEnabled = false;"
        "const off = showPipHome(host, USED, '1.1.0', '2026-11-02', SAMPLE);"
        "const saved = localStorage.getItem('nexusvox-pip-whats-new');"
        "pipEnabled = true;"
        "const on = showPipHome(host, USED, '1.1.0', '2026-11-03', SAMPLE);"
        "console.log(JSON.stringify([off, saved, on]))"
    )
    assert got == ["quiet", None, "tour"]


@needs_node
def test_an_unreadable_saved_state_is_treated_as_empty():
    got = _js(
        "store['nexusvox-pip-whats-new'] = '{not json';"
        "const a = readPipTourState();"
        "store['nexusvox-pip-whats-new'] = JSON.stringify({ version: 5, day: null });"
        "console.log(JSON.stringify([a, readPipTourState()]))"
    )
    assert got == [{"version": "", "day": ""}] * 2


@needs_node
def test_a_single_step_has_no_counter():
    got = _js(
        "const host = {};"
        "showPipHome(host, USED, '1.4.0', '2026-11-02', { '1.4.0': [{ title: 'One', text: 'Only.' }] });"
        "console.log(JSON.stringify([find(last().bubble, 'pip-bubble-eyebrow').textContent,"
        " find(last().bubble, 'pip-tour-next').textContent]))"
    )
    assert got == ["What's new", "Done"]
