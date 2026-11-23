"""Pip, the dashboard mascot: the Show Pip setting, the component's poses and bubble slot, and that nothing renders
while it is off. The component runs under Node against a tiny stand-in for the DOM."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import STATIC

from nexusvox.config import EXAMPLE_CONFIG_PATH, Config, load_config, save_config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

CSS = (STATIC / "style.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
PIP_JS = (STATIC / "dashboard-pip.js").read_text(encoding="utf-8")


# ── The setting ──────────────────────────────────────────────────────


def test_show_pip_is_on_by_default_and_round_trips(tmp_path):
    assert Config().show_pip is True
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    assert cfg.show_pip is True
    cfg.show_pip = False
    save_config(cfg, path)
    assert "show_pip = false" in path.read_text(encoding="utf-8")
    assert load_config(path).show_pip is False


def test_a_garbled_show_pip_stays_on(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nshow_pip = "no"\n', encoding="utf-8")
    assert load_config(path).show_pip is True


def test_the_template_ships_the_key_switched_on():
    assert re.search(r"^show_pip = true$", EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), re.M)


def test_dashboard_switches_pip_and_saves_it(db, session_factory, no_config_save):
    config = Config()
    app = _create_app(DashboardAPI(session_factory, config, db=db))
    with app.test_client() as client:
        assert client.get("/api/settings").get_json()["show_pip"] is True
        assert client.post("/api/settings/show-pip", json={"enabled": False}).get_json()["show_pip"] is False
        assert config.show_pip is False
        assert client.get("/api/settings").get_json()["show_pip"] is False
        assert client.post("/api/settings/show-pip", json={"enabled": True}).get_json()["show_pip"] is True
        assert config.show_pip is True


def test_a_failed_save_leaves_the_running_setting_as_it_was(db, session_factory, monkeypatch):
    config = Config()
    api = DashboardAPI(session_factory, config, db=db)

    def refuse(_config):
        raise OSError("config.toml is read-only")

    monkeypatch.setattr("nexusvox.dashboard.api_settings.save_config", refuse)
    with pytest.raises(OSError):
        api.set_show_pip(False)
    assert config.show_pip is True  # the page puts its switch back, so a reload must agree


@pytest.mark.parametrize("body", [{}, {"enabled": "false"}, {"enabled": 0}])
def test_show_pip_needs_a_real_boolean(db, session_factory, no_config_save, body):
    config = Config()
    app = _create_app(DashboardAPI(session_factory, config, db=db))
    with app.test_client() as client:
        assert client.post("/api/settings/show-pip", json=body).status_code == 400
    assert config.show_pip is True


def test_settings_has_a_show_pip_switch_with_one_short_sentence_of_help():
    assert 'id="show-pip-toggle"' in HTML
    advanced = re.search(r'id="settings-advanced">(.*?)\n      </div>\n', HTML, re.S).group(1)
    assert 'id="show-pip-toggle"' in advanced  # a dashboard look option, not a dictation one
    row = re.search(r"<h3>Show Pip</h3>\s*<p>(.*?)</p>", HTML, re.S)
    assert row, "the Show Pip row is missing"
    assert row.group(1).count(". ") <= 1  # at most two short sentences, no paragraph
    settings = (STATIC / "dashboard-settings.js").read_text(encoding="utf-8")
    assert "s.show_pip" in settings and "/api/settings/show-pip" in settings


# ── The component ────────────────────────────────────────────────────

# A stand-in for the DOM, just enough for createPip / mountPip: elements keep their children, class, style, markup
# and hidden flag. A text node is a plain object, so `bubble instanceof Node` works for a real Node only.
FAKE_DOM = r"""
class Node { constructor() { this.children = []; } }
class Text extends Node { constructor(t) { super(); this.text = t; } }
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.className = ""; this.innerHTML = ""; this.hidden = false;
    this.isConnected = true; this.styleVars = {}; this.style = { setProperty: (k, v) => { this.styleVars[k] = v; } }; }
  append(...kids) { this.children.push(...kids); }
  prepend(...kids) { this.children.unshift(...kids); }
  replaceChildren(...kids) { this.children = kids; }
}
const document = { createElement: (t) => new El(t), createTextNode: (t) => new Text(t) };
const find2 = (e) => e.children.flatMap((c) => (c instanceof El ? [c, ...find2(c)] : []));
const all = (el) => [el, ...find2(el)];
"""


def run_js(body: str):
    source = PIP_JS.replace("const PIP_POSES", "var PIP_POSES")  # share one scope with the body below
    program = f"{FAKE_DOM}\n{source}\n{body}"
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_every_pose_draws_and_the_poses_differ():
    got = run_js("const out = {}; for (const p of PIP_POSES) out[p] = pipSvg(p);console.log(JSON.stringify(out));")
    assert set(got) == {"idle", "blink", "tilt", "sleeping"}
    assert len(set(got.values())) == 4
    for pose, svg in got.items():
        assert svg.startswith("<svg") and svg.endswith("</svg>"), pose
        assert 'class="pip-body"' in svg and 'aria-hidden="true"' in svg, pose
    assert "pip-eyes-open" in got["idle"] and "pip-eyes-closed" in got["idle"]  # the blink swaps these two
    assert "pip-eyes-open" not in got["blink"] and "pip-lid" in got["blink"]
    assert "rotate(-9 128 240)" in got["tilt"] and "rotate(-9" not in got["idle"]
    assert "pip-zzz" in got["sleeping"] and "pip-zzz" not in got["idle"]


@needs_node
def test_an_unknown_pose_falls_back_to_idle_and_the_size_stays_small():
    got = run_js(
        'const a = createPip({ pose: "dancing", size: 400 }); const b = createPip({ size: 10 });'
        "const c = createPip({ size: Number.NaN });"
        "console.log(JSON.stringify([a.className, a.styleVars['--pip-size'], b.styleVars['--pip-size'],"
        " c.styleVars['--pip-size']]));"
    )
    assert got == ["pip pip-idle", "96px", "64px", "80px"]


@needs_node
def test_the_bubble_slot_takes_text_or_a_node_and_is_absent_when_empty():
    got = run_js(
        'const text = createPip({ bubble: "Hello <b>there</b>" });'
        'const node = createPip({ bubble: new El("span") });'
        'const none = createPip({ bubble: "" }); const nul = createPip({});'
        "const slot = (p) => all(p).filter((e) => e.className === 'pip-bubble');"
        "console.log(JSON.stringify({"
        "  text: slot(text).length, textBody: slot(text)[0].children[0].text,"
        "  node: slot(node)[0].children[0].tag, none: slot(none).length, nul: slot(nul).length }));"
    )
    # Text goes in as a text node, so markup in it is never parsed.
    assert got == {"text": 1, "textBody": "Hello <b>there</b>", "node": "span", "none": 0, "nul": 0}


@needs_node
def test_nothing_renders_while_pip_is_off_and_it_comes_back_when_switched_on():
    got = run_js(
        "const host = new El('div');"
        'mountPip(host, { pose: "idle", bubble: "Hi" });'
        "const shown = [host.hidden, all(host).filter((e) => e.className === 'pip-bubble').length];"
        "setShowPip(false);"
        "const off = [host.hidden, host.children.length, createPip({ bubble: 'x' }) === null];"
        "const other = new El('div'); mountPip(other, { bubble: 'Later' });"
        "const offOther = [other.hidden, other.children.length];"
        "setShowPip(true);"
        "const back = [host.hidden, host.children.length, other.hidden, other.children.length];"
        "console.log(JSON.stringify({ shown, off, offOther, back }));"
    )
    assert got["shown"] == [False, 1]
    assert got["off"] == [True, 0, True]  # host empty and hidden, createPip returns nothing
    assert got["offOther"] == [True, 0]
    assert got["back"] == [False, 1, False, 1]


@needs_node
def test_a_host_built_off_the_page_is_still_hidden_when_pip_is_switched_off():
    got = run_js(
        "const host = new El('div'); host.isConnected = false;"  # a card built before it is inserted
        "setShowPip(true); mountPip(host, { bubble: 'Hi' }); setShowPip(true);"
        "host.isConnected = true;"
        "const shown = [host.hidden, host.children.length];"
        "setShowPip(false);"
        "const off = [host.hidden, host.children.length];"
        "console.log(JSON.stringify({ shown, off, hosts: livePipHosts().length }));"
    )
    assert got == {"shown": [False, 1], "off": [True, 0], "hosts": 1}


@needs_node
def test_an_unmounted_host_is_left_alone_by_later_changes():
    got = run_js(
        "const host = new El('div'); mountPip(host, { bubble: 'Hi' }); unmountPip(host);"
        "setShowPip(false); setShowPip(true);"
        "console.log(JSON.stringify([host.children.length, host.hidden, livePipHosts().length]));"
    )
    assert got == [0, False, 0]


@needs_node
def test_only_the_switch_off_value_hides_pip():
    got = run_js(
        "const out = []; for (const v of [undefined, null, true, false]) { setShowPip(v); out.push(pipEnabled); }"
        "console.log(JSON.stringify(out));"
    )
    assert got == [True, True, True, False]  # a settings reply without the key must not hide him


# ── The look ─────────────────────────────────────────────────────────


def test_pip_colours_are_the_stylesheet_tokens_not_hex_in_the_script():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", PIP_JS)
    for token, value in (("--pip-mint-dark", "#00a881"), ("--pip-ink", "#14122b"), ("--green", "#00d2a0")):
        assert re.search(rf":root\s*\{{[^}}]*{token}:\s*{value}", CSS), token
    assert re.search(r"\.pip-body\s*\{\s*fill:\s*var\(--green\)", CSS)


def test_idle_motion_is_a_two_pixel_bob_and_a_blink_that_stop_for_reduced_motion():
    assert re.search(r"@keyframes pip-breathe\s*\{[^}]*\}[^}]*translateY\(2px\)", CSS)
    assert "@keyframes pip-blink " in CSS
    media = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", CSS[CSS.index("pip-breathe") :], re.S)
    assert media, "no reduced-motion block for Pip"
    for selector in (".pip-art", ".pip-eyes-open", ".pip-eyes-closed"):
        assert selector in media.group(1), selector
    assert "animation: none" in media.group(1)
    # Only idle and sleeping move; the still poses carry no animation. The bubble fades in once, and that stops too.
    animated = re.findall(r"^\.(pip-[a-z]+) [^{]*\{[^}]*animation:", CSS, re.M)
    assert set(animated) <= {"pip-idle", "pip-sleeping", "pip-bubble"}
    assert ".pip-bubble" in media.group(1)


@needs_node
def test_the_bubble_sits_on_the_side_asked_for_and_defaults_to_the_right():
    got = run_js(
        "const right = createPip({ bubble: 'x' }); const left = createPip({ bubble: 'x', side: 'left' });"
        "const odd = createPip({ bubble: 'x', side: 'up' });"
        "console.log(JSON.stringify([right.className, left.className, odd.className]));"
    )
    assert got == ["pip pip-idle", "pip pip-idle pip-bubble-left", "pip pip-idle"]


def _rule(selector: str) -> str:
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)\}}", CSS, re.M | re.S)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_the_bubble_is_a_quiet_card_with_a_soft_tail_and_one_gentle_entrance():
    assert re.search(r":root\s*\{[^}]*--pip-bubble-border:\s*#2a2a33", CSS)
    assert re.search(r":root\s*\{[^}]*--surface3:\s*#1c1c22", CSS)
    card = _rule(".pip-bubble")
    assert "background: var(--surface3)" in card and "var(--pip-bubble-border)" in card
    assert "border-radius: var(--radius-sm)" in card  # 12px
    assert "max-width: min(280px, 100%)" in card and "font-size: 13.5px" in card and "color: var(--text)" in card
    assert "box-shadow" in card and "animation: pip-bubble-in" in card
    assert re.search(r"@keyframes pip-bubble-in\s*\{[^}]*opacity: 0[^}]*scale\(0\.94\)", CSS)
    assert ".pip-bubble::before" in CSS and ".pip-bubble-left .pip-bubble::before" in CSS  # a tail on either side
    assert ".pip-bubble-close" in CSS


def test_pip_stands_in_the_bottom_right_corner_of_home_in_the_flow_and_never_over_a_card():
    host = _rule(".home-pip")
    assert "align-self: flex-end" in host and "margin-top: auto" in host
    assert "position" not in host  # in the flow: it cannot cover a card, a button or the sidebar
    assert re.search(
        r"#tab-home\.active\s*\{[^}]*display: flex[^}]*flex-direction: column[^}]*min-height: calc\(100vh", CSS
    )
    home = HTML[HTML.index('<section id="tab-home"') : HTML.index('<section id="tab-settings"')]
    assert home.rindex('id="home-pip"') > home.rindex('id="home-tip-next"')  # the last thing on Home
    # Narrow windows stack the bubble above him instead of beside him, so nothing overlaps or runs off.
    assert re.search(
        r"@media \(max-width: 640px\)\s*\{\s*\.home-pip \.pip-bubble-left\s*\{[^}]*flex-direction: column", CSS
    )
