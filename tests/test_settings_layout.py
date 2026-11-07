"""The Settings tab is grouped; regrouping must not lose a control the script talks to."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_js import dashboard_source

STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = dashboard_source()


def _settings_section() -> str:
    start = HTML.index('<section id="tab-settings"')
    return HTML[start : HTML.index("</section>", start)]


def test_settings_groups_in_order():
    titles = re.findall(r'<h4 class="nexus-section-title">([^<]+)</h4>', _settings_section())
    top = [t for t in titles if t in ("Dictation", "Text &amp; Polish", "Vocabulary", "Models", "Meetings", "Privacy")]
    assert top == ["Dictation", "Vocabulary", "Text &amp; Polish", "Models", "Meetings", "Privacy"]


def test_advanced_is_collapsed_and_holds_rare_options():
    section = _settings_section()
    match = re.search(r'<details class="settings-card settings-advanced"([^>]*)>(.*?)</details>', section, re.S)
    assert match, "Advanced section missing"
    assert " open" not in match.group(1)
    for element_id in ("device-select", "polish-terminals-toggle", "os-commands-toggle"):
        assert f'id="{element_id}"' in match.group(2)


def test_llm_dropdown_lives_in_the_models_card():
    section = _settings_section()
    start = section.index('<h4 class="nexus-section-title">Models</h4>')
    card = section[start : section.index("meeting-settings-card", start)]
    assert 'id="model-select"' in card and 'id="llm-model-select"' in card
    assert card.index('id="model-select"') < card.index('id="llm-model-select"')
    assert "Language model (LLM)" in card and "Used by Cleanup, Polish and upload cleanup" in card
    assert 'id="llm-model-input"' not in HTML


def test_every_script_lookup_has_an_element():
    ids = set(re.findall(r'\bid="([^"]+)"', HTML))
    wanted = set(re.findall(r"""getElementById\(["']([^"']+)["']\)""", JS))
    wanted |= set(re.findall(r"""querySelector(?:All)?\(["']#([\w-]+)""", JS))
    # Elements the script creates itself are not in the static page.
    created = set(re.findall(r'\.id = "([^"]+)"', JS))
    assert not (wanted - ids - created), sorted(wanted - ids - created)


def test_stylesheet_has_no_control_characters():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    bad = [c for c in css if ord(c) < 32 and c not in "\r\n\t"]
    assert not bad, bad


def test_settings_divs_are_balanced():
    section = _settings_section()
    assert len(re.findall(r"<div[\s>]", section)) == len(re.findall(r"</div>", section))


def _feedback_tab() -> str:
    start = HTML.index('<section id="tab-feedback"')
    return HTML[start : HTML.index("</section>", start)]


def test_feedback_is_its_own_tab_not_a_settings_card():
    assert 'id="send-feedback"' not in _settings_section()
    assert 'id="feedback-card"' not in HTML


def test_feedback_entry_and_tab_start_hidden_at_the_sidebar_bottom():
    nav = re.search(r'<nav class="tabs tabs-footer">(.*?)</nav>', HTML, re.S)
    assert nav, "footer nav missing"
    button = re.search(r"<button[^>]*data-tab=\"feedback\"[^>]*>", nav.group(1))
    assert button and " hidden" in button.group(0)
    assert HTML.index("tabs-footer") > HTML.index('data-tab="review"')
    assert " hidden" in re.search(r'<section id="tab-feedback"[^>]*>', HTML).group(0)


def test_feedback_tab_content():
    tab = _feedback_tab()
    assert "Feedback &amp; bug reports" in tab
    assert "nexusvox@nightshift-ai.de" in tab
    assert re.search(r'<button[^>]*id="send-feedback"', tab)
    assert re.search(r'<button[^>]*id="open-feedback-form"', tab)  # the web form stays as the fallback
    assert re.search(r'<input[^>]*id="feedback-include-log"', tab)
    assert " checked" not in re.search(r'<input[^>]*id="feedback-include-log"[^>]*>', tab).group(0)  # off by default
    assert (
        "Attach the log file" not in tab
        and "open-log-folder-feedback" not in tab
        and "file" not in re.findall(r'type="(\w+)"', tab)
    )


def test_sidebar_is_pinned_to_the_viewport_and_its_nav_scrolls():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    header = re.search(r"\nheader \{(.*?)\n\}", css, re.S).group(1)
    assert "position: sticky" in header and "height: 100vh" in header
    tabs = re.search(r"\n\.tabs \{(.*?)\n\}", css, re.S).group(1)
    assert "overflow-y: auto" in tabs and "flex: 1" in tabs
    footer = re.search(r"\n\.tabs-footer \{(.*?)\n\}", css, re.S).group(1)
    assert "margin-top: auto" in footer


SUBMENU = ["Dictation", "Vocabulary", "Text &amp; Polish", "Models", "Meetings", "Privacy", "Advanced"]


def test_submenu_has_one_link_per_card_in_card_order():
    nav = re.search(r'<div class="subtabs" id="settings-submenu"[^>]*>(.*?)</div>', HTML, re.S).group(1)
    links = re.findall(r'<a class="subtab" href="#settings" data-card="([^"]+)">([^<]+)</a>', nav)
    assert [t for _, t in links] == SUBMENU
    section = _settings_section()
    positions = []
    for card_id, _ in links:
        assert f'id="{card_id}"' in section, card_id
        positions.append(section.index(f'id="{card_id}"'))
    assert positions == sorted(positions)
    assert HTML.index('data-tab="settings"') < HTML.index("settings-submenu") < HTML.index('data-tab="analytics"')


def test_search_field_comes_first_and_no_match_message_exists():
    section = _settings_section()
    assert section.index('id="settings-search"') < section.index("settings-card-dictation")
    assert re.search(r'<p id="settings-no-match"[^>]*hidden[^>]*>No setting found</p>', section)


def _run_js(steps: str):
    """Run the real search/submenu functions of the dashboard scripts against a tiny fake DOM; `steps` prints JSON."""
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    start = JS.index("// -- Settings submenu and search")
    end = JS.index('document.querySelectorAll("#settings-submenu .subtab").forEach((link)')
    tail_start = JS.index("// Text a user can see")
    tail_end = JS.index('document.getElementById("settings-search").addEventListener("input"')
    funcs = JS[start:end] + JS[tail_start:tail_end]
    fake = r"""
    class Node {
      constructor(tag, kids = [], attrs = {}) {
        this.nodeType = 1; this.tagName = tag; this.childNodes = kids;
        this.children = kids.filter((k) => k.nodeType === 1);
        this.hidden = !!attrs.hidden; this.open = false; this.cls = new Set(attrs.cls || []); this.top = attrs.top ?? 0;
        this.scrolled = 0; this.dataset = attrs.dataset || {};
        this.classList = { add: (c) => this.cls.add(c), remove: (c) => this.cls.delete(c),
          contains: (c) => this.cls.has(c), toggle: (c, on) => (on ? this.cls.add(c) : this.cls.delete(c)) };
      }
      get textContent() { return this.childNodes.map((k) => (k.nodeType === 3 ? k.data : k.textContent)).join(" "); }
      querySelector() { return this.children[0]; }
      getBoundingClientRect() { return { top: this.top }; }
      scrollIntoView() { this.scrolled++; }
    }
    const T = (data) => ({ nodeType: 3, data });
    const E = (tag, kids, attrs) => new Node(tag, kids, attrs);
    const row = (...kids) => E("DIV", kids);
    const card = (t, ...units) => E("DIV", [E("H4", [T(t)]), ...units]);
    const select = E("SELECT", [E("OPTION", [T("space")])]);
    const cards = {
      dict: card("Dictation", E("P", [T("The everyday basics")]), row(E("H3", [T("Hotkey")]), select),
        row(E("H3", [T("Mode")]), E("BUTTON", [T("Cancel")]), E("SPAN", [T("latency")], { hidden: true }))),
      voc: card("Vocabulary", row(E("H3", [T("Dictionary")]), E("DIV", [T("secretword")], { cls: ["dict-list"] }))),
    };
    const adv = E("DETAILS", [E("SUMMARY", [T("Advanced")]), E("P", [T("Rarely needed")]),
      row(E("H3", [T("Compute device")])), E("P", [T("Takes effect after restart")])]);
    adv.top = 900; cards.dict.top = 0; cards.voc.top = 400;
    const allCards = [cards.dict, cards.voc, adv];
    const noMatch = { hidden: true };
    const input = { value: "" };
    const byId = { "settings-search": input, "settings-no-match": noMatch, "settings-advanced": adv,
      "tab-settings": new Node("SECTION", [], { cls: ["active"] }) };
    const links = allCards.map((c, i) => new Node("A", [], { dataset: { card: "c" + i } }));
    allCards.forEach((c, i) => (byId["c" + i] = c));
    const window = { scrollY: 0, innerHeight: 800 };
    const document = { getElementById: (id) => byId[id], documentElement: { scrollHeight: 1500 },
      querySelectorAll: (sel) => (sel === "#settings-submenu .subtab" ? links : allCards) };
    const hiddenState = () => Object.fromEntries(allCards.map((c, i) => ["c" + i, c.cls.has("search-hidden")]));
    const unitsHidden = (c) => c.children.filter((u) => u.cls.has("search-hidden"))
      .map((u) => u.tagName + ":" + u.textContent.trim().slice(0, 12));
    const search = (q) => { input.value = q; applySettingsSearch(); };
    """
    out = subprocess.run(["node", "-e", fake + funcs + steps], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def test_search_matches_labels_and_hints_only():
    r = _run_js(
        """
        const res = {};
        for (const q of ["hotkey", "everyday", "space", "cancel", "latency", "secretword"]) {
          search(q); res[q] = [hiddenState(), noMatch.hidden];
        }
        console.log(JSON.stringify(res));
        """
    )
    assert r["hotkey"][0] == {"c0": False, "c1": True, "c2": True}
    assert r["everyday"][0]["c0"] is False  # a card-level hint is searchable
    for word in ("space", "cancel", "latency", "secretword"):  # options, buttons, hidden parts, user data
        assert r[word][0] == {"c0": True, "c1": True, "c2": True} and r[word][1] is False


def test_search_hides_orphan_hints_and_units_that_do_not_match():
    r = _run_js('search("compute"); console.log(JSON.stringify([unitsHidden(adv), hiddenState(), adv.open]));')
    assert r[0] == ["P:Rarely neede", "P:Takes effect"]
    assert r[1] == {"c0": True, "c1": True, "c2": False} and r[2] is True


def test_no_match_message_and_clearing_restores_everything():
    r = _run_js(
        """
        search("zzzz"); const none = [hiddenState(), noMatch.hidden];
        search(""); console.log(JSON.stringify([none, hiddenState(), noMatch.hidden, allCards.map(unitsHidden)]));
        """
    )
    assert all(r[0][0].values()) and r[0][1] is False
    assert not any(r[1].values()) and r[2] is True and r[3] == [[], [], []]


def test_clearing_restores_advanced_state_unless_opened_on_purpose():
    r = _run_js(
        """
        search("compute"); const during = adv.open; search(""); const closedAgain = adv.open;
        search("compute"); scrollToSettingsCard("c2"); search(""); const keptOpen = adv.open;
        console.log(JSON.stringify([during, closedAgain, keptOpen]));
        """
    )
    assert r == [True, False, True]


def test_scroll_to_card_lifts_a_filter_that_hides_it():
    r = _run_js(
        'search("compute"); scrollToSettingsCard("c0");'
        "console.log(JSON.stringify([input.value, cards.dict.scrolled, hiddenState()]));"
    )
    assert r[0] == "" and r[1] == 1 and not any(r[2].values())


def test_active_entry_follows_scroll_reaches_last_cards_and_ignores_other_tabs():
    r = _run_js(
        """
        const active = () => links.findIndex((l) => l.cls.has("active"));
        const res = {};
        markActiveSubtab(); res.top = active();
        cards.voc.top = 50; markActiveSubtab(); res.second = active();
        window.scrollY = 700; adv.top = 500; markActiveSubtab(); res.bottom = active();
        byId["tab-settings"].cls.delete("active"); links.forEach((l) => l.cls.delete("active"));
        markActiveSubtab(); res.hiddenTab = active();
        console.log(JSON.stringify(res));
        """
    )
    assert r == {"top": 0, "second": 1, "bottom": 2, "hiddenTab": -1}
