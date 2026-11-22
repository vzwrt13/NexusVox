"""The wordmark is "Nexus" in the text colour and "Vox" in the accent, on the dashboard header and the setup screen."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "nexusvox" / "dashboard" / "static"


def test_setup_wordmark_uses_the_shared_vox_class():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'class="setup-brand-name">Nexus<span class="brand-vox">Vox</span>', html)
    assert re.search(r"<h1>Nexus<span class=\"brand-vox\">Vox</span></h1>", html)


def test_vox_is_the_accent_colour_and_no_rule_recolours_it():
    css = {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("*.css")}
    assert ".brand-vox { color: var(--accent); }" in css["style.css"]
    assert re.search(r"--accent:\s*#6c5ce7", css["style.css"])
    for name, text in css.items():
        for selector in re.findall(r"([^{}]*brand-vox[^{}]*)\{", text):
            assert name == "style.css" and selector.strip() == ".brand-vox", f"{name} restyles .brand-vox"
        assert "setup-brand-name span" not in text
