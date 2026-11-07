"""The Home tab: it is the page the dashboard opens on, and its pure helpers, run under Node."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import STATIC, dashboard_source

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def test_home_is_the_first_tab_and_the_one_that_opens():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    tabs = re.findall(r'<button class="tab([^"]*)" data-tab="([^"]+)"', html)
    assert tabs[0] == (" active", "home")
    assert [name for cls, name in tabs if "active" in cls] == ["home"]
    assert re.search(r'<section id="tab-home" class="tab-content active">', html)
    assert re.search(r'<section id="tab-settings" class="tab-content">', html)


def test_home_has_the_cards_and_panels_its_script_fills():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "home-status",
        "home-dictate",
        "home-hotkey-keycaps",
        "home-record",
        "home-upload",
        "home-recent",
        "home-week-words",
        "home-week-saved",
        "home-tip-text",
        "home-tip-next",
    ):
        assert f'id="{element_id}"' in html, element_id
    source = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    for element_id in set(re.findall(r'getElementById\("(home-[a-z-]+)"\)', source)):
        assert f'id="{element_id}"' in html, element_id


def test_activating_home_loads_it():
    assert re.search(r'if \(name === "home"\) openHomeTab\(\);', dashboard_source())


def test_the_tip_list_is_a_fixed_english_list():
    tips = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    block = re.search(r"const HOME_TIPS = \[(.*?)\n\];", tips, re.S)
    assert block, "HOME_TIPS not found"
    assert len(re.findall(r'^  "', block.group(1), re.M)) >= 5


def run_js(body: str):
    source = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    helpers = "".join(
        re.search(rf"^function {name}\(.*?^}}\n", source, re.S | re.M).group(0)
        for name in ("describeHomeStatus", "localIsoDate", "formatSavedMinutes")
    )
    out = subprocess.run(
        ["node", "-e", f"{helpers}\n{body}"], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    )
    return json.loads(out.stdout)


@needs_node
def test_status_says_loaded_device_and_offline():
    got = run_js(
        'const m = { name: "Parakeet", custom: false };'
        'console.log(JSON.stringify(describeHomeStatus(m, "cuda", { status: "idle" })))'
    )
    assert got == {"state": "ok", "text": "Model loaded · Parakeet · GPU · runs offline"}


@needs_node
def test_status_is_yellow_while_loading_and_red_on_error():
    got = run_js(
        'const m = { name: "Whisper", custom: false };'
        'console.log(JSON.stringify([describeHomeStatus(m, "cpu", { status: "downloading" }),'
        ' describeHomeStatus(m, "cpu", { status: "error" })]))'
    )
    assert got[0] == {"state": "warn", "text": "Loading the speech model · Whisper · CPU · runs offline"}
    assert got[1]["state"] == "down"


@needs_node
def test_a_custom_server_is_not_called_offline():
    got = run_js('console.log(JSON.stringify(describeHomeStatus({ name: "Mine", custom: true }, "cpu", null)))')
    assert got["text"].endswith("uses your own server")
    assert "offline" not in got["text"]


@needs_node
def test_saved_time_and_local_dates():
    got = run_js(
        "console.log(JSON.stringify([formatSavedMinutes(0), formatSavedMinutes(12.4), formatSavedMinutes(135),"
        " formatSavedMinutes(-3), formatSavedMinutes(undefined), localIsoDate(new Date(2026, 0, 5))]))"
    )
    assert got == ["0 min", "12 min", "2 h 15 min", "0 min", "0 min", "2026-01-05"]
