"""The dashboard's pending/failed row states: the pure helpers of dashboard.js, run under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static" / "dashboard.js"
START = "// ── Pending and failed states"
END = "// ── end of pending and failed states"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def run_js(body: str):
    source = JS.read_text(encoding="utf-8")
    helpers = source[source.index(START) : source.index(END)]
    script = f"{helpers}\n{body}"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_states_are_told_apart():
    states = run_js(
        'console.log(JSON.stringify(["x", {pending: "Summarising"}, {error: "Offline", retry: "again"}, {}]'
        ".map(availabilityState)))"
    )
    assert states == [
        {"kind": "off", "text": "x"},
        {"kind": "pending", "text": "Summarising"},
        {"kind": "error", "text": "Offline", "retry": "again"},
        {"kind": "off", "text": "Not available"},
    ]


def test_only_pending_rows_count():
    result = run_js(
        """console.log(JSON.stringify([
          rowsHavePending([{exports_unavailable: {a: "No"}, actions_unavailable: {}}]),
          rowsHavePending([{exports_unavailable: {a: {error: "x"}}}]),
          rowsHavePending([{exports_unavailable: {a: {pending: "x"}}}]),
          rowsHavePending([{actions_unavailable: {a: {pending: "x"}}}, {}]),
          rowsHavePending([]),
        ]))"""
    )
    assert result == [False, False, True, True, False]


def test_polling_starts_with_a_pending_row_and_stops_without_one():
    result = run_js(
        """
        const queue = []; let cleared = 0, reloads = 0;
        const timers = {set: (fn, ms) => { queue.push([fn, ms]); return queue.length; }, clear: () => { cleared++; }};
        const poller = createPendingPoller(() => { reloads++; }, 4000, timers);
        const log = [];
        poller.update(false); log.push(poller.active);        // nothing pending: idle
        poller.update(true); poller.update(true);             // pending: one timer only
        log.push(poller.active, queue.length, queue[0][1]);
        queue[0][0](); log.push(poller.active, reloads);      // it fires and reloads
        poller.update(true); poller.update(false);            // pending again, then finished: cancelled
        log.push(poller.active, cleared);
        console.log(JSON.stringify(log));
        """
    )
    assert result == [False, True, 1, 4000, False, 1, False, 1]
