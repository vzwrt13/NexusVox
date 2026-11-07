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
    helpers = re.search(r"^const HOME_BUSY_PHASES = .*?;$", source, re.M).group(0) + helpers
    out = subprocess.run(
        ["node", "-e", f"{helpers}\n{body}"], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    )
    return json.loads(out.stdout)


@needs_node
def test_status_names_model_device_and_offline_without_claiming_green():
    got = run_js(
        'const m = { name: "Parakeet", custom: false };'
        'console.log(JSON.stringify(describeHomeStatus(m, "cuda", { status: "idle" })))'
    )
    # Nothing probes the container or the custom server, so an idle model is shown neutral, never green.
    assert got == {"text": "Speech model · Parakeet · GPU · runs offline"}


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


def test_no_hash_and_a_disabled_tab_open_home_through_restore_tab_from_hash():
    source = dashboard_source()
    restore = re.search(r"^function restoreTabFromHash\(.*?^}$", source, re.S | re.M).group(0)
    assert restore.count('activateTab("home")') == 2
    assert "homeLoaded" not in source


def test_the_home_cards_are_valid_html_inside_buttons():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for button in re.findall(r'<button type="button" class="home-card.*?</button>', html, re.S):
        assert not re.search(r"<(h[1-6]|p|div)[ >]", button), button


def test_the_keycap_loop_exists_once():
    source = dashboard_source()
    assert source.count('className: "keycaps-plus"') == 1
    assert "renderKeycapsInto(" in (STATIC / "dashboard-home.js").read_text(encoding="utf-8")


def test_the_first_tip_follows_the_local_day():
    assert "getTimezoneOffset()" in (STATIC / "dashboard-home.js").read_text(encoding="utf-8")


def test_the_actions_do_not_repeat_what_opening_recordings_loads():
    source = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    record = re.search(r'getElementById\("home-record"\).*?^}\);', source, re.S | re.M).group(0)
    assert record.index("await refreshMeetingStatus()") < record.index("meetingToggleBtn.click()")
    assert "openUploadDialog" not in source  # it refreshes what openRecordingsTab already refreshed
    # a recent row opens its recording through showRecording alone (it opens the tab itself)
    assert re.search(r"if \(!\(await showRecording\(item\.id\)\)\) goToTab\(\"recordings\"\)", source)
