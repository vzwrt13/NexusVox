"""An upload action's error lands next to the recording it was run on (run under Node)."""

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
        [_fn(source, "feedbackEls"), _fn(source, "showUploadError"), _fn(source, "runUploadAction", True)]
    )
    stubs = """
    const box = () => ({ style: { display: "none" }, textContent: "" });
    const uploadErrorEl = box(), uploadNoteEl = box(), detailErrorEl = box(), detailNoteEl = box();
    const extensionUploadActions = [{ id: "act", label: "Act", panel: "p" }];
    const uploadActionHandlers = { act: async () => { throw new Error("boom"); } };
    const runningUploadActions = new Set();
    const loadFileTranscriptions = () => {};
    // The detail pane of the chosen recording, with its own error and note boxes.
    const detail = {
      querySelector: (sel) => ({ ".rec-detail-error": detailErrorEl, ".rec-detail-note": detailNoteEl })[sel] || null,
      querySelectorAll: () => [],
    };
    // A button inside the detail pane, or one outside it.
    const button = (inDetail) => ({
      dataset: { action: "act" },
      closest: (sel) => (sel === ".rec-detail" && inDetail ? detail : null),
    });
    """
    script = f"{stubs}\n{parts}\n(async () => {{ {body} }})();"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_error_of_an_action_in_the_detail_pane_shows_in_the_detail_pane():
    result = run_js(
        """await runUploadAction(button(true), { id: 1 });
        console.log(JSON.stringify([detailErrorEl.textContent, detailErrorEl.style.display,
          uploadErrorEl.textContent, uploadErrorEl.style.display]))"""
    )
    assert result == ["Act failed: boom", "block", "", "none"]


def test_error_from_outside_the_detail_pane_shows_in_the_list_pane():
    result = run_js(
        """await runUploadAction(button(false), { id: 2 });
        console.log(JSON.stringify([uploadErrorEl.textContent, uploadErrorEl.style.display,
          detailErrorEl.textContent, detailErrorEl.style.display]))"""
    )
    assert result == ["Act failed: boom", "block", "", "none"]
