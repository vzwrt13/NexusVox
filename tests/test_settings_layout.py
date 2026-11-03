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


def test_feedback_card_is_hidden_until_the_script_has_a_url():
    section = _settings_section()
    assert re.search(r'<div class="settings-card" id="feedback-card" hidden>', section)
    assert 'id="send-feedback"' in section
    assert "feedbackCard.hidden = !s.feedback_url" in JS
