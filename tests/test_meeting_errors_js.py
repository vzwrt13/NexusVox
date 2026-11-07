"""An upload action's error lands next to its recording, or in the list pane once that is no longer shown (Node)."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import dashboard_source

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def _fn(source: str, name: str, is_async: bool = False) -> str:
    prefix = "async function" if is_async else "function"
    match = re.search(rf"^{prefix} {name}\(.*?^}}\n", source, re.S | re.M)
    assert match, f"{name} not found"
    return match.group(0)


def run_js(body: str):
    source = dashboard_source()
    parts = "\n".join(
        [
            _fn(source, "feedbackEls"),
            _fn(source, "hideFeedback"),
            _fn(source, "showFeedback"),
            _fn(source, "showUploadError"),
            _fn(source, "runUploadAction", True),
        ]
    )
    stubs = """
    const box = () => ({ style: { display: "none" }, textContent: "" });
    const uploadErrorEl = box(), uploadNoteEl = box(), detailErrorEl = box(), detailNoteEl = box();
    const extensionUploadActions = [{ id: "act", label: "Act", panel: "p" }];
    const uploadActionHandlers = { act: async () => { throw new Error("boom"); } };
    const runningUploadActions = new Set();
    const loadFileTranscriptions = () => {};
    // The detail pane shows recording `shown`, with its own error and note boxes.
    let shown = "1";
    const detail = {
      get dataset() { return { id: shown }; },
      querySelector: (sel) => ({ ".rec-detail-error": detailErrorEl, ".rec-detail-note": detailNoteEl })[sel] || null,
      querySelectorAll: () => [],
    };
    const pane = { querySelector: () => detail };
    const document = { getElementById: (id) => (id === "rec-detail" ? pane : null) };
    const button = { dataset: { action: "act" }, closest: () => detail };
    """
    script = f"{stubs}\n{parts}\n(async () => {{ {body} }})();"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_error_of_an_action_shows_in_the_detail_pane_while_it_shows_the_recording():
    result = run_js(
        """await runUploadAction(button, { id: 1, title: "Sync" });
        console.log(JSON.stringify([detailErrorEl.textContent, detailErrorEl.style.display,
          uploadErrorEl.textContent, uploadErrorEl.style.display]))"""
    )
    assert result == ["Act failed: boom", "block", "", "none"]


def test_error_of_an_action_whose_recording_was_left_shows_in_the_list_pane_with_its_name():
    result = run_js(
        """uploadActionHandlers.act = async () => { shown = "2"; throw new Error("boom"); };  // user moved on
        await runUploadAction(button, { id: 1, title: null, original_filename: "call.wav" });
        console.log(JSON.stringify([uploadErrorEl.textContent, uploadErrorEl.style.display,
          detailErrorEl.textContent, detailErrorEl.style.display]))"""
    )
    assert result == ["call.wav: Act failed: boom", "block", "", "none"]
