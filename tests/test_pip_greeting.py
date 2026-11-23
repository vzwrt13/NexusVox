"""Pip's daily speech bubble on Home: the counts it is made from (the endpoint), the texts file, and the choice
(one bubble a day, dismissal, the order, the never-used detection), run under Node with stand-ins for the page."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import STATIC

from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.analytics import get_pip_facts
from nexusvox.dashboard.analytics_overview import _TYPING_WPM
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.storage.models import COMMAND_APP, DictionaryEntryRow, FileTranscription, Transcription

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

TEXTS = (STATIC / "dashboard-pip-texts.js").read_text(encoding="utf-8")
GREETING = (STATIC / "dashboard-pip-greeting.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


# ── The counts ───────────────────────────────────────────────────────


def _add(session_factory, *rows):
    with session_factory() as session:
        session.add_all(rows)
        session.commit()


def test_facts_of_an_empty_database_are_all_zero_and_carry_the_typing_speed(session_factory):
    facts = get_pip_facts(session_factory)
    assert {k: v for k, v in facts.items() if k != "typing_wpm"} == dict.fromkeys(
        [
            "words",
            "dictations",
            "meetings",
            "uploads",
            "dictionary_entries",
            "formatting_commands",
            "week_words",
            "week_dictations",
            "week_time_saved_minutes",
        ],
        0,
    )
    assert facts["typing_wpm"] == _TYPING_WPM


def test_facts_count_what_exists_and_never_return_what_was_said(session_factory):
    secret = "my secret dictation sentence"
    _add(
        session_factory,
        Transcription(text=secret, language="en", duration_ms=1000),
        Transcription(text="two words", language="en", duration_ms=1000),
        Transcription(text="Hello. New paragraph. Bye", language="en", duration_ms=1000),
        Transcription(text="[command] new paragraph", language="en", duration_ms=0, app=COMMAND_APP),
        FileTranscription(
            text="meeting secret", language="en", duration_ms=1, original_filename="a.wav", kind="meeting"
        ),
        FileTranscription(text="file secret", language="en", duration_ms=1, original_filename="b.wav", kind="upload"),
        FileTranscription(text="file secret", language="en", duration_ms=1, original_filename="c.wav", kind="upload"),
        DictionaryEntryRow(wrong="secret wrong", right="secret right"),
    )
    facts = get_pip_facts(session_factory)
    assert facts["words"] == 10  # the command row is no dictation
    assert facts["dictations"] == 3
    assert (facts["meetings"], facts["uploads"], facts["dictionary_entries"]) == (1, 2, 1)
    assert facts["formatting_commands"] == 1
    assert facts["week_words"] == 10 and facts["week_dictations"] == 3
    assert all(isinstance(value, int | float) for value in facts.values())
    assert "secret" not in json.dumps(facts)


def test_formatting_commands_are_found_in_saved_dictations_in_both_languages_and_nowhere_else(session_factory):
    def used(*rows: Transcription) -> int:
        with session_factory() as session:
            session.query(Transcription).delete()
            session.add_all(rows)
            session.commit()
        return get_pip_facts(session_factory)["formatting_commands"]

    dictation = lambda text, **kw: Transcription(text=text, language="en", duration_ms=1000, **kw)  # noqa: E731
    assert used() == 0
    assert used(dictation("Tabletop games are fun")) == 0  # "tab" is not a formatting phrase here
    assert used(dictation("send it, new paragraph, thanks")) == 1
    assert used(dictation("Dann neue Zeile und weiter")) == 1
    assert used(dictation("Und neuer Absatz")) == 1
    assert used(dictation("[nexus] new line", app=COMMAND_APP)) == 0  # a command row is no dictation
    assert used(dictation("[polish] new paragraph", polish_status="applied")) == 0


def test_the_dictation_totals_are_kept_until_a_dictation_is_added(session_factory):
    _add(session_factory, Transcription(text="one two", language="en", duration_ms=500))
    first = get_pip_facts(session_factory)
    assert get_pip_facts(session_factory) == first
    _add(session_factory, Transcription(text="three four five", language="en", duration_ms=500))
    again = get_pip_facts(session_factory)
    assert (first["words"], again["words"], again["dictations"]) == (2, 5, 2)


def test_the_endpoint_serves_the_facts(db, session_factory):
    _add(session_factory, Transcription(text="one two three", language="en", duration_ms=500))
    app = _create_app(DashboardAPI(session_factory, Config(), db=db))
    with app.test_client() as client:
        got = client.get("/api/pip/facts").get_json()
    assert got["words"] == 3 and got["dictations"] == 1 and got["typing_wpm"] == _TYPING_WPM


# ── The texts ────────────────────────────────────────────────────────


def _js(body: str):
    program = f"{TEXTS}\n{GREETING}\n{body}"
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_every_text_is_short_english_counts_only_and_never_mentions_a_paid_plan():
    lists = _js("console.log(JSON.stringify({ m: PIP_MILESTONES, t: PIP_TIPS, s: PIP_STATS }))")
    texts = [x["text"] for x in lists["m"] + lists["t"]] + lists["s"]
    ids = [x["id"] for x in lists["m"] + lists["t"]]
    assert len(ids) == len(set(ids)), "an id is kept for good and must be unique"
    for text in texts:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
        assert 1 <= len(sentences) <= 2, text
        assert len(text) <= 170, text
        assert not re.search(r"\b(pro|premium|upgrade|subscription|licen[cs]e|paid|price)\b", text, re.I), text
    # A milestone and a tip name a count the endpoint serves.
    facts = set(
        re.findall(
            r'"([a-z_]+)"',
            (STATIC.parent / "analytics_pip.py").read_text(encoding="utf-8"),
        )
    )
    for item in lists["m"]:
        assert item["counter"] in facts, item["id"]
    for item in lists["t"]:
        assert item["unused"] in facts, item["id"]


@needs_node
def test_the_tips_cover_the_four_free_features():
    tips = _js("console.log(JSON.stringify(PIP_TIPS.map((t) => t.unused)))")
    assert sorted(tips) == ["dictionary_entries", "formatting_commands", "meetings", "uploads"]


# ── The choice ───────────────────────────────────────────────────────

BUSY = (
    "{ words: 20000, dictations: 300, meetings: 2, uploads: 1, dictionary_entries: 5, formatting_commands: 9,"
    " week_words: 2400, week_time_saved_minutes: 31.4, typing_wpm: 40 }"
)


@needs_node
def test_a_milestone_comes_first_and_is_said_once():
    got = _js(
        "const facts = { words: 0, dictations: 1, meetings: 1, uploads: 1,"
        " dictionary_entries: 1, formatting_commands: 1 };"
        "const a = pickPipGreeting(facts, emptyPipGreetingState(), '2026-11-02');"
        "const b = pickPipGreeting(facts, a.state, '2026-11-03');"
        "console.log(JSON.stringify([a.greeting.id, b.greeting.id, b.state.seen]))"
    )
    # Several milestones are reached: one a day, in list order, none twice.
    assert got[0] == "first-dictation" and got[1] == "first-meeting"
    assert got[2] == ["first-dictation", "first-meeting"]


@needs_node
def test_an_existing_history_is_not_told_about_its_first_steps_and_10000_words_is_said():
    got = _js(
        "const ids = []; let state = emptyPipGreetingState();"
        f"const facts = {BUSY};"
        "for (let d = 1; d <= 7; d++) { const r = pickPipGreeting(facts, state, '2026-11-0' + d);"
        " state = r.state; ids.push(r.greeting && r.greeting.id); }"
        "console.log(JSON.stringify([ids, state.seen]))"
    )
    ids, seen = got
    # Nothing is saved yet: of what is reached, only the highest goal of each counter is still said, one a day.
    assert "first-dictation" not in ids and "words-1000" not in ids
    assert ids[:4] == ["first-meeting", "first-file", "dictations-100", "words-10000"]
    assert ids[4] == "stat-week"  # words-50000 is not reached; no tip is due: every feature is used
    assert {"first-dictation", "words-1000"} <= set(seen)


@needs_node
def test_a_new_user_loses_no_milestone_to_the_catch_up():
    got = _js(
        "const none = { words: 0, dictations: 0, meetings: 0, uploads: 0,"
        " dictionary_entries: 1, formatting_commands: 1 };"
        "console.log(JSON.stringify([backfilledPipSeen(none), backfilledPipSeen({ ...none, dictations: 1, words: 40 }),"
        " backfilledPipSeen({ ...none, dictations: 150, words: 1500 })]))"
    )
    assert got == [[], [], ["first-dictation"]]


@needs_node
def test_a_tip_for_a_never_used_feature_comes_when_no_milestone_is_due_and_names_only_unused_features():
    got = _js(
        "const none = { words: 0, dictations: 0, meetings: 0, uploads: 0,"
        " dictionary_entries: 0, formatting_commands: 0 };"
        "const used = { ...none, formatting_commands: 3, dictionary_entries: 2, meetings: 0, uploads: 0 };"
        "let state = emptyPipGreetingState(); const first = [];"
        "for (let d = 1; d <= 4; d++) { const r = pickPipGreeting(used, state, '2026-11-0' + d); state = r.state;"
        " first.push(r.greeting && r.greeting.id); }"
        "console.log(JSON.stringify(first))"
    )
    # Voice commands and own words are used, so only the file and meeting tips remain, each said once; then quiet.
    assert got == ["tip-file-upload", "tip-meetings", None, None]


@needs_node
def test_the_never_used_detection_follows_each_count():
    got = _js(
        "const base = { words: 0, dictations: 0, meetings: 0, uploads: 0,"
        " dictionary_entries: 1, formatting_commands: 1 };"
        "const seen = ['first-meeting', 'first-file'];"
        "const pick = (extra) => { const r = pickPipGreeting({ ...base, ...extra },"
        " { ...emptyPipGreetingState(), seen }, '2026-11-02'); return r.greeting && r.greeting.id; };"
        "console.log(JSON.stringify([pick({}), pick({ uploads: 4, meetings: 1 }),"
        " pick({ dictionary_entries: 0, uploads: 1, meetings: 1 }),"
        " pick({ formatting_commands: 0, uploads: 1, meetings: 1 })]))"
    )
    assert got == ["tip-file-upload", None, "tip-own-words", "tip-voice-commands"]


@needs_node
def test_the_statistic_is_the_last_resort_states_its_basis_and_is_skipped_without_a_week():
    got = _js(
        "const used = { words: 0, dictations: 0, meetings: 1, uploads: 1,"
        " dictionary_entries: 1, formatting_commands: 1 };"
        "const seen = ['first-meeting', 'first-file'];"
        "const s = (extra) => pickPipGreeting({ ...used, ...extra, typing_wpm: 40 },"
        " { ...emptyPipGreetingState(), seen }, '2026-11-02').greeting;"
        "console.log(JSON.stringify([s({ week_words: 1234, week_time_saved_minutes: 25.4 }),"
        " s({ week_words: 0, week_time_saved_minutes: 0 }), s({ week_words: 5, week_time_saved_minutes: 0.2 }),"
        " s({ week_words: 5000, week_time_saved_minutes: 95 })]))"
    )
    assert got[0]["id"] == "stat-week"
    assert "1,234" in got[0]["text"] and "25 min" in got[0]["text"] and "40 words a minute" in got[0]["text"]
    assert got[1] is None and got[2] is None  # no dictation this week, or a saving too small to claim
    assert "1 h 35 min" in got[3]["text"]
    assert "typing_wpm" in TEXTS and "_TYPING_WPM" in TEXTS  # the estimate's basis is stated next to the texts


@needs_node
def test_a_statistic_template_may_use_a_placeholder_twice():
    got = _js(
        "PIP_STATS.length = 0; PIP_STATS.push('{saved} saved: {words} words, that is {saved}! {wpm}/{wpm}');"
        "const facts = { week_words: 1234, week_time_saved_minutes: 25.4, typing_wpm: 40 };"
        "console.log(JSON.stringify(pipStatText(facts, '2026-11-02')))"
    )
    assert got == "25 min saved: 1,234 words, that is 25 min! 40/40"


@needs_node
def test_a_stat_is_not_remembered_so_it_can_come_back_next_week_but_a_day_keeps_its_bubble():
    got = _js(
        "const used = { words: 0, dictations: 0, meetings: 1, uploads: 1,"
        " dictionary_entries: 1, formatting_commands: 1,"
        " week_words: 800, week_time_saved_minutes: 10, typing_wpm: 40 };"
        "const seen = ['first-meeting', 'first-file'];"
        "const a = pickPipGreeting(used, { ...emptyPipGreetingState(), seen }, '2026-11-02');"
        "const b = pickPipGreeting(used, a.state, '2026-11-03');"
        "const again = pickPipGreeting({ ...used, week_words: 900 }, a.state, '2026-11-02');"
        "console.log(JSON.stringify([a.greeting.id, b.greeting.id, b.state.seen,"
        " again.greeting.text === a.greeting.text]))"
    )
    assert got == ["stat-week", "stat-week", ["first-meeting", "first-file"], True]


@needs_node
def test_a_day_has_one_bubble_even_when_a_new_milestone_is_reached_later_that_day():
    got = _js(
        "const none = { words: 0, dictations: 0, meetings: 1, uploads: 1,"
        " dictionary_entries: 1, formatting_commands: 1 };"
        "const a = pickPipGreeting({ ...none, dictations: 1 }, emptyPipGreetingState(), '2026-11-02');"
        "const b = pickPipGreeting({ ...none, dictations: 1, words: 1000 }, a.state, '2026-11-02');"
        "const c = pickPipGreeting({ ...none, dictations: 1, words: 1000 }, a.state, '2026-11-03');"
        "console.log(JSON.stringify([a.greeting.id, b.greeting.id, c.greeting.id]))"
    )
    assert got[:2] == ["first-dictation", "first-dictation"] and got[2] in {"first-meeting", "first-file", "words-1000"}


@needs_node
def test_a_day_with_nothing_to_say_saves_nothing():
    got = _js(
        "const used = { words: 0, dictations: 0, meetings: 1, uploads: 1,"
        " dictionary_entries: 1, formatting_commands: 1 };"
        "const seen = ['first-meeting', 'first-file'];"
        "const r = pickPipGreeting(used, { ...emptyPipGreetingState(), seen }, '2026-11-02');"
        "console.log(JSON.stringify([r.greeting, r.state.day, r.state.seen]))"
    )
    assert got == [None, "", ["first-meeting", "first-file"]]


# ── On the page ──────────────────────────────────────────────────────

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
const document = { createElement: (tag) => ({ tag, children: [], className: "", textContent: "",
  listeners: {}, attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; },
  append(...k) { this.children.push(...k); }, addEventListener(t, f) { this.listeners[t] = f; } }) };
const FACTS = { words: 0, dictations: 1, meetings: 1, uploads: 1, dictionary_entries: 1, formatting_commands: 1 };
const last = () => mounts[mounts.length - 1];
const part = (bubble, cls) => bubble.children.find((c) => c.className === cls);
"""


@needs_node
def test_dismissing_keeps_the_bubble_gone_for_the_rest_of_the_day_and_pip_sits_quietly():
    got = _js(
        PAGE + "const host = {};"
        "const shown = showPipGreeting(host, FACTS);"
        "const withBubble = last();"
        "part(withBubble.bubble, 'pip-bubble-close').listeners.click();"  # the × button
        "const afterDismiss = last();"
        "const reopened = showPipGreeting(host, FACTS);"  # the same day, Home opened again
        "const reopenedMount = last();"
        "showPipGreeting(host, { ...FACTS, words: 1000 });"  # a new milestone later the same day
        "const laterMount = last();"
        "const nextDay = showPipGreeting(host, FACTS, '2026-11-03');"
        "console.log(JSON.stringify({ shown: shown && shown.id, tilt: withBubble.pose,"
        " text: part(withBubble.bubble, 'pip-bubble-message').textContent,"
        " afterDismiss: [afterDismiss.pose, afterDismiss.bubble], reopened, reopenedMount: [reopenedMount.pose,"
        " reopenedMount.bubble], later: laterMount.bubble, nextDay: nextDay && nextDay.id,"
        " stored: JSON.parse(store['nexusvox-pip-greeting']).day }))"
    )
    assert got["shown"] == "first-dictation" and got["tilt"] == "tilt"
    assert got["text"].startswith("Your first dictation")
    assert got["afterDismiss"] == ["idle", None]
    assert got["reopened"] is None and got["reopenedMount"] == ["idle", None]
    assert got["later"] is None  # dismissed for the day: not even a newer milestone
    assert got["nextDay"] == "first-meeting"  # the next day a new bubble, the next one not said before
    assert got["stored"] == "2026-11-03"  # the last bubble chosen


@needs_node
def test_the_bubble_has_a_labelled_x_button_and_pip_stands_in_the_right_corner():
    got = _js(
        PAGE + "const host = {}; showPipGreeting(host, FACTS); const m = last();"
        "const close = part(m.bubble, 'pip-bubble-close');"
        "console.log(JSON.stringify([m.side, close.className, close.textContent, close.attrs, close.type]))"
    )
    assert got[0] == "left"  # Pip is bottom right, so his bubble is on his left
    assert got[1] == "pip-bubble-close" and got[2] == "×"
    assert got[3] == {"aria-label": "Dismiss"} and got[4] == "button"
    assert "Got it" not in GREETING


@needs_node
def test_the_bubble_style_follows_the_kind_of_content_milestone_c_tip_and_statistic_a():
    got = _js(
        PAGE + "const used = { words: 0, dictations: 0, meetings: 1, uploads: 1, dictionary_entries: 1,"
        " formatting_commands: 1, week_words: 900, week_time_saved_minutes: 12, typing_wpm: 40 };"
        "const said = ['first-meeting', 'first-file'];"
        "const show = (facts, seen) => { localStorage.setItem('nexusvox-pip-greeting',"
        " JSON.stringify({ day: '', id: '', text: '', dismissed: false, seen }));"
        " const host = {}; const g = showPipGreeting(host, facts, '2026-11-02'); const b = last().bubble;"
        " return { id: g.id, kind: g.kind, style: g.style, cls: b.className,"
        "   eyebrow: (part(b, 'pip-bubble-eyebrow') || {}).textContent || null,"
        "   close: !!part(b, 'pip-bubble-close') }; };"
        "const milestone = show({ ...used, dictations: 5 }, said);"
        "const tip = show({ ...used, uploads: 0 }, ['first-meeting', 'first-dictation']);"
        "const stat = show(used, said);"
        "console.log(JSON.stringify({ milestone, tip, stat }))"
    )
    assert got["milestone"] == {
        "id": "first-dictation",
        "kind": "milestone",
        "style": "c",
        "cls": "pip-bubble-body pip-bubble-style-c",
        "eyebrow": "Milestone",
        "close": True,
    }
    for kind in ("tip", "stat"):
        assert got[kind]["style"] == "a" and got[kind]["cls"] == "pip-bubble-body pip-bubble-style-a", kind
        assert got[kind]["eyebrow"] is None and got[kind]["close"], kind  # style A has no label line
    assert (got["tip"]["kind"], got["stat"]["kind"]) == ("tip", "stat")


@needs_node
def test_every_kind_of_content_has_a_bubble_style_and_only_c_may_have_a_label():
    got = _js("console.log(JSON.stringify({ styles: PIP_BUBBLE_STYLES, labels: PIP_LABELS }))")
    assert got["styles"] == {"milestone": "c", "tip": "a", "stat": "a"}
    assert set(got["labels"]) <= {kind for kind, style in got["styles"].items() if style == "c"}


@needs_node
def test_the_bubble_is_one_component_whose_style_only_changes_its_label_line():
    got = _js(
        PAGE + "const a = buildPipBubble('Hi', () => {}); const c = buildPipBubble('Hi', () => {},"
        " { style: 'c', label: 'Milestone' });"
        "const odd = buildPipBubble('Hi', () => {}, { style: 'zzz', label: 'X' });"
        "const names = (b) => b.children.map((e) => e.className);"
        "console.log(JSON.stringify([a.className, names(a), c.className, names(c), odd.className, names(odd)]))"
    )
    assert got == [
        "pip-bubble-body pip-bubble-style-a",
        ["pip-bubble-message", "pip-bubble-close"],
        "pip-bubble-body pip-bubble-style-c",
        ["pip-bubble-eyebrow", "pip-bubble-message", "pip-bubble-close"],
        "pip-bubble-body pip-bubble-style-a",  # an unknown style is the plain one, and its label is not drawn
        ["pip-bubble-message", "pip-bubble-close"],
    ]


@needs_node
def test_dismiss_in_a_stale_window_does_not_overwrite_a_newer_state():
    got = _js(
        PAGE + "const host = {}; showPipGreeting(host, FACTS, '2026-11-02');"
        "const dismiss = part(last().bubble, 'pip-bubble-close').listeners.click;"
        # another window moves on: the next day, another milestone said
        "showPipGreeting(host, FACTS, '2026-11-03'); const newer = JSON.parse(store['nexusvox-pip-greeting']);"
        "dismiss();"
        "const after = JSON.parse(store['nexusvox-pip-greeting']);"
        "console.log(JSON.stringify([newer, after, last().bubble]))"
    )
    newer, after, bubble = got
    assert newer["day"] == "2026-11-03" and newer["seen"] == ["first-dictation", "first-meeting"]
    assert after == newer  # the old window's × did not write its own, older state
    assert bubble is None  # but it still hides its own bubble


@needs_node
def test_an_undismissed_bubble_is_still_there_on_the_next_visit_of_the_day():
    got = _js(
        PAGE + "const host = {}; showPipGreeting(host, FACTS);"
        "const first = part(last().bubble, 'pip-bubble-message').textContent;"
        "showPipGreeting(host, FACTS);"
        "console.log(JSON.stringify([first, part(last().bubble, 'pip-bubble-message').textContent, mounts.length]))"
    )
    assert got[0] == got[1] and got[2] == 2


@needs_node
def test_without_facts_or_with_damaged_saved_state_pip_sits_quietly_or_starts_fresh():
    got = _js(
        PAGE + "const host = {}; const a = showPipGreeting(host, null); const quiet = last();"
        "store['nexusvox-pip-greeting'] = '{broken'; const b = showPipGreeting(host, FACTS);"
        "store['nexusvox-pip-greeting'] = '[1,2]'; const c = showPipGreeting(host, FACTS, '2026-11-09');"
        "console.log(JSON.stringify([a, quiet.bubble, b && b.id, c && c.id]))"
    )
    assert got == [None, None, "first-dictation", "first-dictation"]


# ── Wiring ───────────────────────────────────────────────────────────


def test_home_has_a_pip_host_that_starts_hidden_and_the_scripts_load_before_home():
    assert re.search(r'<div class="home-pip" id="home-pip" hidden></div>', HTML)
    order = re.findall(r'<script src="(dashboard-[a-z-]+\.js)"', HTML)
    assert (
        order.index("dashboard-pip.js")
        < order.index("dashboard-pip-texts.js")
        < order.index("dashboard-pip-greeting.js")
    )
    assert order.index("dashboard-pip-greeting.js") < order.index("dashboard-home.js")
    home = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    assert '"/api/pip/facts"' in home and "showPipHome(host, facts," in home
    assert re.search(r"function openHomeTab\(\) \{.*loadHomePip\(known\);", home, re.S)


def test_the_greeting_never_asks_for_texts_and_inserts_text_as_text():
    assert "innerHTML" not in GREETING
    assert "/api/pip/facts" not in GREETING  # the page's Home fetches the counts; this file only chooses
    for forbidden in ("transcription", "recent", "file-transcriptions", "/api/history"):
        assert forbidden not in GREETING and forbidden not in TEXTS


@needs_node
def test_a_greeting_is_not_used_up_while_show_pip_is_off():
    got = _js(
        PAGE + "const host = {};"
        "pipEnabled = false;"
        "const off = showPipGreeting(host, FACTS);"
        "const saved = localStorage.getItem('nexusvox-pip-greeting');"
        "pipEnabled = true;"
        "const on = showPipGreeting(host, FACTS);"
        "console.log(JSON.stringify([off, saved, on && on.id]))"
    )
    assert got[0] is None and got[1] is None and got[2]
