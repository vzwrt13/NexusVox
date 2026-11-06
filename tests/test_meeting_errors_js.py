"""An upload action's error lands in the tab of the list it was started from (dashboard.js, run under Node)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static" / "dashboard.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def _fn(source: str, name: str, is_async: bool = False) -> str:
    prefix = "async function" if is_async else "function"
    match = re.search(rf"^{prefix} {name}\(.*?^}}\n", source, re.S | re.M)
    assert match, f"{name} not found"
    return match.group(0)


def run_js(body: str):
    source = JS.read_text(encoding="utf-8")
    parts = "\n".join(
        [_fn(source, "feedbackEls"), _fn(source, "showUploadError"), _fn(source, "runUploadAction", True)]
    )
    stubs = """
    const box = () => ({ style: { display: "none" }, textContent: "" });
    const uploadErrorEl = box(), uploadNoteEl = box(), meetingErrorEl = box(), meetingNoteEl = box();
    const extensionUploadActions = [{ id: "act", label: "Act", panel: "p" }];
    const uploadActionHandlers = { act: async () => { throw new Error("boom"); } };
    const runningUploadActions = new Set();
    const loadFileTranscriptions = () => {};
    // A button inside a list; `tab` is the tab section that holds it.
    const button = (tab) => ({
      dataset: { action: "act" },
      closest: (sel) => (sel === "#tab-meetings" ? (tab === "meetings" ? {} : null) : null),
    });
    """
    script = f"{stubs}\n{parts}\n(async () => {{ {body} }})();"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_error_from_the_meetings_list_shows_in_the_meetings_tab():
    result = run_js(
        """await runUploadAction(button("meetings"), { id: 1 });
        console.log(JSON.stringify([meetingErrorEl.textContent, meetingErrorEl.style.display,
          uploadErrorEl.textContent, uploadErrorEl.style.display]))"""
    )
    assert result == ["Act failed: boom", "block", "", "none"]


def test_error_from_the_upload_list_still_shows_in_the_upload_tab():
    result = run_js(
        """await runUploadAction(button("upload"), { id: 2 });
        console.log(JSON.stringify([uploadErrorEl.textContent, uploadErrorEl.style.display,
          meetingErrorEl.textContent, meetingErrorEl.style.display]))"""
    )
    assert result == ["Act failed: boom", "block", "", "none"]
