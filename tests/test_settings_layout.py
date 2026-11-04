"""The Settings tab is grouped; regrouping must not lose a control the script talks to."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "dashboard.js").read_text(encoding="utf-8")


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
    assert "Send feedback or report a bug" in tab
    assert "nexusvox@nightshift-ai.de" in tab
    assert re.search(r'<button[^>]*id="send-feedback"', tab)
    assert re.search(r'<button[^>]*id="open-log-folder-feedback"', tab)
    assert 'id="feedback-log-dir"' in tab


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


def _run_search(query: str, advanced_text: str = "Compute device"):
    """Run applySettingsSearch of dashboard.js against a tiny fake DOM."""
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    start = JS.index("let advancedWasOpen")
    end = JS.index("function clearSettingsSearch")
    funcs = JS[start:end]
    script = (
        """
    const mk = (text) => {
      const cls = new Set();
      return { textContent: text, open: false, tagName: "DIV", _cls: cls,
        classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
        querySelector: () => ({ textContent: text.split("|")[0] }) };
    };
    const units = { dict: [mk("Hotkey|Press a key"), mk("Mode|hold or toggle")], voc: [mk("Dictionary|words")],
      adv: [mk("ADVTEXT|device")] };
    const cards = { dict: mk("Dictation|x"), voc: mk("Vocabulary|x"), adv: mk("Advanced|x") };
    const cardUnits = new Map(Object.keys(cards).map((k) => [cards[k], units[k]]));
    const noMatch = { hidden: true };
    const input = { value: QUERY };
    const document = { getElementById: (id) => id === "settings-search" ? input
      : id === "settings-no-match" ? noMatch : cards.adv };
    const settingsCards = () => Object.values(cards);
    const settingsUnits = (card) => cardUnits.get(card);
    const markActiveSubtab = () => {};
    FUNCS
    applySettingsSearch();
    console.log(JSON.stringify({
      hidden: Object.fromEntries(Object.entries(cards).map(([k, c]) => [k, c._cls.has("search-hidden")])),
      noMatch: !noMatch.hidden, advancedOpen: cards.adv.open,
    }));
    """.replace("ADVTEXT", advanced_text)
        .replace("QUERY", json.dumps(query))
        .replace("FUNCS", funcs)
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_search_match_hides_other_cards():
    r = _run_search("hotkey")
    assert r["hidden"] == {"dict": False, "voc": True, "adv": True}
    assert not r["noMatch"]


def test_search_without_match_shows_message():
    r = _run_search("zzzz")
    assert all(r["hidden"].values()) and r["noMatch"]


def test_search_match_in_advanced_opens_it():
    r = _run_search("compute")
    assert r["hidden"] == {"dict": True, "voc": True, "adv": False}
    assert r["advancedOpen"] is True


def test_empty_search_restores_everything():
    r = _run_search("")
    assert not any(r["hidden"].values()) and not r["noMatch"]
