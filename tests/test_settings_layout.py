"""The Settings tab is grouped; regrouping must not lose a control the script talks to."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "dashboard.js").read_text(encoding="utf-8")


def _settings_section() -> str:
    start = HTML.index('<section id="tab-settings"')
    return HTML[start : HTML.index("</section>", start)]


def test_settings_groups_in_order():
    titles = re.findall(r'<h4 class="nexus-section-title">([^<]+)</h4>', _settings_section())
    top = [t for t in titles if t in ("Dictation", "Text &amp; Polish", "Vocabulary", "Models", "Meetings", "Privacy")]
    assert top == ["Dictation", "Text &amp; Polish", "Vocabulary", "Models", "Meetings", "Privacy"]


def test_advanced_is_collapsed_and_holds_rare_options():
    section = _settings_section()
    match = re.search(r'<details class="settings-card settings-advanced"([^>]*)>(.*?)</details>', section, re.S)
    assert match, "Advanced section missing"
    assert " open" not in match.group(1)
    for element_id in ("device-select", "polish-terminals-toggle", "llm-model-input", "os-commands-toggle"):
        assert f'id="{element_id}"' in match.group(2)


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
