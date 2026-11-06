"""Tab switching opens every tab at the top: activateTab, run under Node with a stubbed page."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import dashboard_source

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def run_js(body: str):
    source = dashboard_source()
    match = re.search(r"^function activateTab\(.*?^}\n", source, re.S | re.M)
    assert match, "activateTab not found"
    stubs = """
    const calls = [];
    const classList = () => ({ add() {}, remove() {} });
    const el = { hidden: false, classList: classList() };
    const document = {
      querySelector: () => el,
      getElementById: () => el,
      querySelectorAll: () => [],
    };
    const window = { scrollTo: (x, y) => calls.push(["scrollTo", x, y]) };
    for (const f of ["loadAnalytics", "loadFlaggedTranscriptions", "openMeetingsTab", "loadMeetingSettings",
      "loadFileTranscriptions", "refreshDiarizeToggle", "resumeUploadJob", "loadReviewTranscriptions",
      "loadBenchmarks", "openExtensionPanel", "markActiveSubtab"]) globalThis[f] = () => {};
    globalThis.loadLlmStatus = () => Promise.resolve();
    """
    script = f"{stubs}\n{match.group(0)}\n{body}"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_every_tab_switch_scrolls_to_the_top():
    calls = run_js(
        'for (const t of ["meetings", "upload", "settings", "analytics"]) activateTab(t);'
        "console.log(JSON.stringify(calls))"
    )
    assert calls == [["scrollTo", 0, 0]] * 4


def test_a_deliberate_jump_keeps_the_scroll():
    calls = run_js('activateTab("settings", { keepScroll: true }); console.log(JSON.stringify(calls))')
    assert calls == []


def test_meeting_settings_link_and_transcript_jump_are_the_deliberate_jumps():
    source = dashboard_source()
    assert source.count("keepScroll: true") == 2
    assert 'activateTab("settings", { keepScroll: true })' in source
    assert "showTranscript(id)" in source
