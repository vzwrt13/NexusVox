"""The dashboard scripts as a set: the fixed load order, and that they run together like the one file they were."""

import json
import re
import shutil
import subprocess
from collections import Counter

import pytest
from dashboard_js import STATIC, dashboard_source, script_names

from nexusvox.dashboard.scripts import page_scripts

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

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

# Runs each script as its own classic script in one context, like the page, then fires DOMContentLoaded. A stand-in
# page answers: an id index.html does not have is null, as in a browser, and querySelectorAll hands back one element,
# so load-time forEach callbacks run. fetch never answers, so what runs after a reply or a user action is NOT covered.
RUNNER = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const [dir, ...names] = process.argv.slice(1);
const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
const stub = () => new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === Symbol.iterator ? [][Symbol.iterator].bind([]) : stub()),
  set: () => true,
  apply: () => stub(),
  construct: () => stub(),
});
const listeners = {};
const byId = (id) => (ids.has(id) ? stub() : null);
const document = new Proxy({}, {
  get: (t, k) => {
    if (k === "addEventListener") return (type, fn) => { (listeners[type] ||= []).push(fn); };
    if (k === "getElementById") return byId;
    if (k === "querySelectorAll") return () => [stub()];
    if (k === "querySelector") return (sel) => { const m = /#([\w-]+)/.exec(sel); return m ? byId(m[1]) : stub(); };
    return stub();
  },
});
const page = { document, getComputedStyle: () => stub(), setInterval: () => 0, setTimeout: () => 0,
  localStorage: { getItem: () => null, setItem() {} }, location: { hash: "", pathname: "/", search: "" },
  addEventListener() {}, fetch: () => new Promise(() => {}), URLSearchParams, console };
page.window = page;
vm.createContext(page);
for (const name of names) vm.runInContext(fs.readFileSync(path.join(dir, name), "utf8"), page, { filename: name });
const loaders = listeners.DOMContentLoaded || [];
loaders.forEach((fn) => fn());
console.log(JSON.stringify({
  ext: Object.keys(page.NexusVoxExt).sort(),
  loaders: loaders.length,
  clocks: vm.runInContext(
    "[formatUploadClock(205.348), formatUploadClock(65.5), formatClock(3661), formatClock(61)]", page),
}));
"""


def test_index_html_loads_the_scripts_in_the_fixed_order():
    assert script_names() == ORDER


def test_every_script_in_the_folder_is_loaded():
    on_disk = {path.name for path in STATIC.glob("*.js")}
    assert on_disk == set(ORDER) | {"chart.min.js"}


def test_chart_js_loads_before_the_dashboard_scripts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert page_scripts(html) == ["chart.min.js", *ORDER]


def test_page_scripts_reads_any_tag_form_and_skips_comments():
    html = """<script defer src="a.js"></script><script type='text/javascript' src='b.js' async></script>
    <!-- <script src="gone.js"></script> --><SCRIPT src="c.js"></SCRIPT><script>inline()</script>"""
    assert page_scripts(html) == ["a.js", "b.js", "c.js"]


def test_no_function_is_declared_twice():
    # Separate files hide this: the file that loads last wins silently, for every caller.
    names = Counter(re.findall(r"^(?:async )?function (\w+)", dashboard_source(), re.M))
    assert [name for name, count in names.items() if count > 1] == []


@needs_node
def test_the_scripts_run_together_in_order():
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
    # A file's duration is a float: Upload rounds it, the Meetings timer shows whole seconds with hours.
    assert result["clocks"] == ["3:25", "1:06", "1:01:01", "01:01"]


def test_tabs_go_live_in_the_last_file():
    last = (STATIC / ORDER[-1]).read_text(encoding="utf-8")
    assert 'querySelectorAll(".tab").forEach(bindTab)' in last
    assert 'addEventListener("hashchange", restoreTabFromHash)' in last
    others = "".join((STATIC / name).read_text(encoding="utf-8") for name in ORDER[:-1])
    assert "forEach(bindTab)" not in others and '"hashchange"' not in others


@needs_node
def test_escape_html_is_safe_inside_a_quoted_attribute():
    helper = re.search(r"^function escapeHtml\(.*?^}\n", dashboard_source(), re.S | re.M).group(0)
    fake = """const escaped = (v) => v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const document = {createElement: () => ({set textContent(v) { this.innerHTML = escaped(v); }})};"""
    body = """console.log(JSON.stringify([escapeHtml('x" onmouseover="y'), escapeHtml("it's"), escapeHtml("<b>&")]))"""
    out = subprocess.run(
        ["node", "-e", f"{fake}\n{helper}\n{body}"], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    assert json.loads(out) == ["x&quot; onmouseover=&quot;y", "it&#39;s", "&lt;b&gt;&amp;"]
