"""The dashboard scripts as a set: the fixed load order, and that they load together like the one file they were."""

import json
import shutil
import subprocess

import pytest
from dashboard_js import STATIC, script_names

ORDER = [
    "dashboard-core.js",
    "dashboard-settings.js",
    "dashboard-settings-models.js",
    "dashboard-settings-vocabulary.js",
    "dashboard-analytics.js",
    "dashboard-edit.js",
    "dashboard-review.js",
    "dashboard-upload.js",
    "dashboard-meetings.js",
    "dashboard-dev.js",
    "dashboard-init.js",
]

# Runs each script as its own classic script in one context, like the page: a name a file uses at load before a
# later file defines it, or a name two files declare with let/const, throws here. The page is a stand-in that
# answers everything; only load-time code runs, nothing the user triggers.
RUNNER = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const [dir, ...names] = process.argv.slice(1);
const stub = () => new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === Symbol.iterator ? [][Symbol.iterator].bind([]) : stub()),
  set: () => true,
  apply: () => stub(),
  construct: () => stub(),
});
const listeners = {};
const document = new Proxy({}, {
  get: (t, k) => k === "addEventListener" ? (type, fn) => { (listeners[type] ||= []).push(fn); }
    : k === "querySelectorAll" ? () => [] : stub(),
});
const page = { document, getComputedStyle: () => stub(), setInterval: () => 0, setTimeout: () => 0,
  location: { hash: "", pathname: "/", search: "" }, addEventListener() {}, fetch: async () => stub(), console };
page.window = page;
vm.createContext(page);
for (const name of names) vm.runInContext(fs.readFileSync(path.join(dir, name), "utf8"), page, { filename: name });
console.log(JSON.stringify({
  ext: Object.keys(page.NexusVoxExt).sort(),
  loaders: (listeners.DOMContentLoaded || []).length,
  clock: vm.runInContext("formatClock(3661)", page),
}));
"""


def test_index_html_loads_the_scripts_in_the_fixed_order():
    assert script_names() == ORDER


def test_every_script_in_the_folder_is_loaded():
    on_disk = {path.name for path in STATIC.glob("*.js")}
    assert on_disk == set(ORDER) | {"chart.min.js"}


def test_chart_js_loads_before_the_dashboard_scripts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="chart.min.js"') < html.index(f'src="{ORDER[0]}"')


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_scripts_load_together_in_order():
    out = subprocess.run(
        ["node", "-e", RUNNER, str(STATIC), *script_names()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert {"registerPanel", "registerUploadAction", "showTranscript"} <= set(result["ext"])
    assert result["loaders"] == 1
    # Upload and Meetings both declare formatClock; the Meetings one has always won, for both tabs.
    assert result["clock"] == "1:01:01"
