"""The Settings tab has one page per section; regrouping must not lose a control the script talks to."""

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
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _settings_section() -> str:
    start = HTML.index('<section id="tab-settings"')
    return HTML[start : HTML.index("</section>", start)]


def test_settings_groups_in_order():
    titles = re.findall(r'<h4 class="nexus-section-title">([^<]+)</h4>', _settings_section())
    top = [t for t in titles if t in ("Dictation", "Text &amp; Polish", "Vocabulary", "Models", "Meetings", "Privacy")]
    assert top == ["Dictation", "Vocabulary", "Text &amp; Polish", "Models", "Meetings", "Privacy"]


def test_advanced_is_the_last_page_and_holds_rare_options():
    section = _settings_section()
    match = re.search(
        r'<div class="settings-card settings-advanced" id="settings-advanced">(.*?)\n      </div>\n', section, re.S
    )
    assert match, "Advanced page missing"
    assert '<h4 class="nexus-section-title">Advanced</h4>' in match.group(1)
    assert "<details" not in section  # every section is a page of its own, none is folded away
    for element_id in ("device-select", "polish-terminals-toggle", "os-commands-toggle"):
        assert f'id="{element_id}"' in match.group(1)


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
    bad = [c for c in CSS if ord(c) < 32 and c not in "\r\n\t"]
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
    header = re.search(r"\nheader \{(.*?)\n\}", CSS, re.S).group(1)
    assert "position: sticky" in header and "height: 100vh" in header
    tabs = re.search(r"\n\.tabs \{(.*?)\n\}", CSS, re.S).group(1)
    assert "overflow-y: auto" in tabs and "flex: 1" in tabs
    footer = re.search(r"\n\.tabs-footer \{(.*?)\n\}", CSS, re.S).group(1)
    assert "margin-top: auto" in footer


SECTIONS = ["Dictation", "Vocabulary", "Text &amp; Polish", "Models", "Meetings", "Privacy", "Advanced"]


def test_nav_has_one_link_per_page_in_page_order():
    section = _settings_section()
    nav = re.search(r'<nav class="settings-nav" id="settings-nav"[^>]*>(.*?)</nav>', section, re.S).group(1)
    link = (
        r'<a class="settings-link(?: active)?" href="#settings/(?P<slug>[^"]+)" data-page="(?P=slug)"'
        r' data-card="(?P<card>[^"]+)"(?: aria-current="page")?>(?P<title>[^<]+)</a>'
    )
    links = [(m["slug"], m["card"], m["title"]) for m in re.finditer(link, nav)]
    assert [t for _, _, t in links] == SECTIONS
    first_link = re.search(r'<a class="settings-link active" href="#settings/dictation"[^>]*aria-current="page"', nav)
    assert first_link, "Dictation must be the open page in the markup"
    assert re.search(r'<div class="settings-card active" id="settings-card-dictation">', section)
    assert section.count("settings-card active") == 1
    assert [slug for slug, _, _ in links] == [
        "dictation", "vocabulary", "text-polish", "models", "meetings", "privacy", "advanced",
    ]  # fmt: skip
    pages = section.split('id="settings-pages"', 1)[1]
    positions = []
    for _, card_id, _ in links:
        assert f'id="{card_id}"' in pages, card_id
        positions.append(pages.index(f'id="{card_id}"'))
    assert positions == sorted(positions)
    assert "settings-submenu" not in HTML and "subtab" not in HTML  # the sidebar lists tabs only


def test_every_page_has_an_icon_on_its_link_and_its_title():
    for card_id in re.findall(r'data-card="([^"]+)"', _settings_section()):
        assert f'.settings-link[data-card="{card_id}"]::before {{ background-image: url("icons/duotone-' in CSS, card_id
        assert f"#{card_id} > .nexus-section-title::before" in CSS, card_id


def test_rerun_setup_entry_exists_and_waits_for_the_setup():
    button = re.search(r'<button[^>]*id="settings-rerun-setup"[^>]*>([^<]*)</button>', _settings_section())
    assert button and " disabled" in button.group(0) and "first-run setup" in button.group(1)


def test_search_field_sits_in_the_nav_and_no_match_message_exists():
    section = _settings_section()
    nav = section.index('id="settings-nav"')
    assert nav < section.index('id="settings-search"') < section.index('id="settings-pages"')
    assert re.search(r'<p id="settings-no-match"[^>]*hidden[^>]*>No setting found</p>', section)


FAKE_DOM = r"""
class Node {
  constructor(tag, kids = [], attrs = {}) {
    this.nodeType = 1; this.tagName = tag; this.childNodes = kids; this.id = attrs.id || "";
    this.children = kids.filter((k) => k.nodeType === 1);
    this.hidden = !!attrs.hidden; this.cls = new Set(attrs.cls || []); this.scrolled = 0;
    this.dataset = attrs.dataset || {}; this.attrs = {};
    this.classList = { add: (c) => this.cls.add(c), remove: (c) => this.cls.delete(c),
      contains: (c) => this.cls.has(c), toggle: (c, on) => (on ? this.cls.add(c) : this.cls.delete(c)) };
  }
  get textContent() { return this.childNodes.map((k) => (k.nodeType === 3 ? k.data : k.textContent)).join(" "); }
  querySelector() { return this.children[0]; }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  scrollIntoView() { this.scrolled++; }
}
const T = (data) => ({ nodeType: 3, data });
const E = (tag, kids, attrs) => new Node(tag, kids, attrs);
const row = (...kids) => E("DIV", kids);
const card = (id, t, ...units) => E("DIV", [E("H4", [T(t)], { cls: ["nexus-section-title"] }), ...units], { id });
const select = E("SELECT", [E("OPTION", [T("space")])]);
const dict = card("c0", "Dictation", E("P", [T("The everyday basics")]), row(E("H3", [T("Hotkey")]), select),
  row(E("H3", [T("Mode")]), E("BUTTON", [T("Cancel")]), E("SPAN", [T("latency")], { hidden: true })));
const voc = card("c1", "Vocabulary",
  row(E("H3", [T("Dictionary")]), E("DIV", [T("secretword")], { cls: ["dict-list"] })));
const adv = card("c2", "Advanced", E("P", [T("Rarely needed")]), row(E("H3", [T("Compute device")])),
  E("P", [T("Takes effect after restart")]));
const allCards = [dict, voc, adv];
const noMatch = { hidden: true };
const input = { value: "" };
const tab = new Node("SECTION", [], { cls: ["active"] });
const byId = { "settings-search": input, "settings-no-match": noMatch, "tab-settings": tab };
allCards.forEach((c) => (byId[c.id] = c));
const slugs = ["dictation", "vocabulary", "advanced"];
const links = allCards.map((c, i) => new Node("A", [], { dataset: { card: c.id, page: slugs[i] } }));
const history = { url: null, replaceState: (a, b, url) => { history.url = url; } };
const document = { getElementById: (id) => byId[id],
  querySelectorAll: (sel) => (sel === "#settings-nav .settings-link" ? links : allCards) };
const hiddenState = () => Object.fromEntries(allCards.map((c) => [c.id, c.cls.has("search-hidden")]));
const openPages = () => allCards.filter((c) => c.cls.has("active")).map((c) => c.id);
const activeLinks = () => links.filter((l) => l.cls.has("active")).map((l) => l.dataset.card);
const unitsHidden = (c) => c.children.filter((u) => u.cls.has("search-hidden"))
  .map((u) => u.tagName + ":" + u.textContent.trim().slice(0, 12));
const search = (q) => { input.value = q; applySettingsSearch(); };
"""


def _run_js(steps: str):
    """Run the real page and search functions of the dashboard scripts against a tiny fake DOM; `steps` prints JSON."""
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    start = JS.index("// -- Settings pages and search")
    end = JS.index("settingsLinks().forEach((link) => {")
    tail_start = JS.index("// Text a user can see")
    tail_end = JS.index('document.getElementById("settings-search").addEventListener("input"')
    funcs = JS[start:end] + JS[tail_start:tail_end]
    out = subprocess.run(
        ["node", "-e", FAKE_DOM + funcs + steps], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


def test_one_page_is_open_at_a_time_and_its_link_is_marked():
    r = _run_js(
        """
        const res = {};
        showSettingsPage("c0"); res.first = [openPages(), activeLinks()];
        showSettingsPage("c2"); res.third = [openPages(), activeLinks()];
        console.log(JSON.stringify(res));
        """
    )
    assert r["first"] == [["c0"], ["c0"]] and r["third"] == [["c2"], ["c2"]]


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


def test_search_hides_orphan_hints_and_the_links_of_pages_without_a_match():
    r = _run_js(
        """
        search("compute");
        console.log(JSON.stringify([unitsHidden(adv), hiddenState(), links.map((l) => l.cls.has("search-hidden")),
          tab.cls.has("searching")]));
        """
    )
    assert r[0] == ["P:Rarely neede", "P:Takes effect"]
    assert r[1] == {"c0": True, "c1": True, "c2": False}
    assert r[2] == [True, True, False] and r[3] is True


def test_no_match_message_and_clearing_restores_everything():
    r = _run_js(
        """
        search("zzzz"); const none = [hiddenState(), noMatch.hidden];
        search(""); console.log(JSON.stringify([none, hiddenState(), noMatch.hidden, allCards.map(unitsHidden),
          links.map((l) => l.cls.has("search-hidden")), tab.cls.has("searching")]));
        """
    )
    assert all(r[0][0].values()) and r[0][1] is False
    assert not any(r[1].values()) and r[2] is True and r[3] == [[], [], []]
    assert r[4] == [False, False, False] and r[5] is False


def test_opening_a_page_lifts_a_filter_that_hides_it():
    r = _run_js(
        'search("compute"); showSettingsPage("c0");'
        "console.log(JSON.stringify([input.value, openPages(), hiddenState(), tab.cls.has('searching')]));"
    )
    assert r[0] == "" and r[1] == ["c0"] and not any(r[2].values()) and r[3] is False


def test_the_open_link_is_announced_with_aria_current():
    r = _run_js(
        """
        showSettingsPage("c1");
        const marks = () => links.map((l) => l.attrs["aria-current"] || null);
        const first = marks(); showSettingsPage("c2");
        console.log(JSON.stringify([first, marks()]));
        """
    )
    assert r == [[None, "page", None], [None, None, "page"]]


def test_page_listeners_run_with_the_id_of_the_opened_page():
    r = _run_js(
        """
        const seen = []; onSettingsPageOpen((id) => seen.push(id));
        showSettingsPage("c1"); showSettingsPage("c0");
        console.log(JSON.stringify(seen));
        """
    )
    assert r == ["c1", "c0"]


def test_open_page_is_kept_in_the_hash_and_opened_from_it():
    r = _run_js(
        """
        showSettingsPage("c2"); const advanced = settingsPageHash();
        showSettingsPageBySlug("vocabulary"); const vocabulary = [settingsPageHash(), openPages()];
        showSettingsPageBySlug("nope"); const unknown = openPages();
        console.log(JSON.stringify([advanced, vocabulary, unknown]));
        """
    )
    assert r == ["#settings/advanced", ["#settings/vocabulary", ["c1"]], ["c1"]]


def test_opening_a_page_from_another_tab_shows_it_sets_the_hash_and_scrolls():
    r = _run_js(
        'openSettingsPageFromElsewhere("c1"); console.log(JSON.stringify([openPages(), history.url, voc.scrolled]));'
    )
    assert r == [["c1"], "#settings/vocabulary", 1]


def test_pollers_of_a_page_wait_for_their_page():
    # The learn-from-edits line and the language-model list are drawn when their page opens and polled only while
    # on screen, since the Settings tab no longer shows every control.
    assert "learnStatus.offsetParent !== null" in JS
    assert re.search(
        r'onSettingsPageOpen\(\(id\) => \{\s*if \(id === "settings-card-models"\) llmRefresher\.wake\(\)', JS
    )
    assert re.search(
        r'onSettingsPageOpen\(\(id\) => \{\s*if \(id === "settings-card-vocabulary"\) loadLearnStatus\(\)', JS
    )


def test_hash_carries_the_page_through_tab_clicks_and_reloads():
    assert 'name === "settings" ? settingsPageHash() : "#" + name' in JS
    assert 'name === "settings" && page) showSettingsPageBySlug(page)' in JS


def test_navigation_stacks_before_the_page_gets_too_narrow():
    assert re.search(r"@media \(max-width: 1000px\) \{\s*\.settings-layout \{ flex-direction: column", CSS)


def test_search_gap_ignores_hidden_cards():
    rule = re.search(r"#tab-settings\.searching #settings-pages > ([^{]+)\{ margin-top: 12px; \}", CSS)
    assert rule and rule.group(1).strip() == ".settings-card:not(.search-hidden) ~ .settings-card:not(.search-hidden)"
