"""Back and Forward across the dashboard pages: the real history functions run against a small fake browser."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import STATIC, dashboard_source

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

# A browser's session history: the entries, the position, popstate on every move and hashchange when the hash differs.
FAKE_BROWSER = r"""
const hashOf = (url) => (url.includes("#") ? url.slice(url.indexOf("#")) : "");
const entries = [{ state: null, hash: "" }];
let at = 0, left = false;
const listeners = { popstate: [], hashchange: [] };
const window = {
  location: { get hash() { return entries[at].hash; }, get href() { return "http://127.0.0.1/" + entries[at].hash; },
    pathname: "/", search: "" },
  addEventListener: (type, fn) => listeners[type].push(fn),
};
function go(step) {
  const next = at + step;
  if (next < 0 || next >= entries.length) { left = true; return; } // the browser leaves the dashboard
  const hash = entries[at].hash;
  at = next;
  listeners.popstate.forEach((fn) => fn({ state: entries[at].state }));
  if (entries[at].hash !== hash) listeners.hashchange.forEach((fn) => fn());
}
const history = {
  get state() { return entries[at].state; },
  pushState(state, _, url) { entries.splice(at + 1); entries.push({ state, hash: hashOf(url) }); at += 1; },
  replaceState(state, _, url) { entries[at] = { state, hash: hashOf(url) }; },
  back: () => go(-1),
  forward: () => go(1),
};
const back = () => go(-1), forward = () => go(1);
// What the page shows.
let shown = null, settingsPage = null, recording = null;
const activations = [];
const activateTab = (name) => {
  name = TAB_ALIASES[name] || name;
  shown = name;
  activations.push(name);
  return ["home", "settings", "analytics", "recordings"].includes(name);
};
const showSettingsPageBySlug = (slug) => { settingsPage = slug; };
let lookups = 0;
const showRecording = (id, options) => {
  lookups += 1;
  recording = [id, { restore: options.restore }];
  // The lookup answers later; a step left meanwhile (options.current) is not acted on.
  return Promise.resolve().then(() => options.current() && id !== "gone");
};
const defaultSettingsSlug = () => "dictation";
const settingsPageHash = () => "#settings/" + (settingsPage || defaultSettingsSlug());
const analyticsViewHash = () => "#analytics";
"""


def _function(source: str, name: str) -> str:
    return re.search(rf"^(?:async )?function {name}\(.*?^}}$", source, re.S | re.M).group(0)


def run_js(body: str):
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    source = dashboard_source()
    names = ("pageOfHash", "samePage", "replaceHash", "markHistoryReady", "guardHistory", "pushPage", "bindTab")
    core = "\n".join(_function(source, n) for n in names)
    core += "\nlet historyGuarded = false, historyReady = false;\n"
    core += re.search(r"^const TAB_ALIASES = .*?;$", source, re.M).group(0)
    restore = _function(source, "restoreTabFromHash") + _function(source, "restoreRecording")
    init = (STATIC / "dashboard-init.js").read_text(encoding="utf-8")
    popstate = re.search(r'^window\.addEventListener\("popstate".*?^}\);$', init, re.S | re.M).group(0)
    wiring = 'window.addEventListener("hashchange", restoreTabFromHash);\n' + popstate
    # The TAB_ALIASES constant has to exist before the fake page uses it.
    script = "\n".join([FAKE_BROWSER.replace("let shown", core + "\nlet shown", 1), restore, wiring, body])
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


# A click on a sidebar tab button, then the real page start (the hash of the first load).
CLICKS = r"""
const click = (tab) => { let fn; bindTab({ dataset: { tab }, addEventListener: (_, f) => (fn = f) }); fn(); };
// The first user action arms the guard, as the pointerdown listener does.
const act = () => guardHistory();
restoreTabFromHash(); markHistoryReady();
"""


@needs_node
def test_back_and_forward_move_between_the_pages_visited():
    r = run_js(
        CLICKS
        + """
        act(); click("settings"); const onSettings = shown;
        back(); const afterBack = shown;
        forward(); const afterForward = shown;
        console.log(JSON.stringify([onSettings, afterBack, afterForward, left]));
        """
    )
    assert r == ["settings", "home", "settings", False]


@needs_node
def test_every_step_is_one_entry_and_the_same_page_adds_none():
    r = run_js(
        CLICKS
        + """
        act(); click("settings"); click("settings"); click("analytics"); click("recordings");
        const trail = [];
        for (let i = 0; i < 3; i++) { back(); trail.push(shown); }
        console.log(JSON.stringify([trail, entries.length]));
        """
    )
    # The start marker and the first page, then settings, analytics and recordings.
    assert r == [["analytics", "settings", "home"], 5]


@needs_node
def test_a_settings_page_and_a_recording_are_steps_of_their_own():
    r = run_js(
        CLICKS
        + """
        act(); settingsPage = "dictation"; click("settings");
        pushPage("#settings/models");                // a Settings page chosen in the sidebar
        pushPage("#recordings/7");                   // a recording opened from Home or Edit
        back(); const first = [shown, settingsPage, recording];
        back(); const second = [shown, settingsPage];
        forward(); forward();
        console.log(JSON.stringify([first, second, recording]));
        """
    )
    assert r[0] == ["settings", "models", None]
    assert r[1] == ["settings", "dictation"]
    # Forward opens the recording again without pushing a step of its own.
    assert r[2] == ["7", {"restore": True}]


@needs_node
def test_back_on_the_first_page_never_leaves_the_dashboard():
    r = run_js(
        CLICKS
        + """
        act(); click("settings");
        back();                                       // Home
        back();                                       // the first page: the marker entry, then straight forward
        const stays = [left, shown, at];
        back(); back(); back();
        console.log(JSON.stringify([stays, left, shown]));
        """
    )
    assert r[0][0] is False and r[0][1] == "home"
    assert r[1] is False and r[2] == "home"


@needs_node
def test_a_reload_stays_on_the_page_and_adds_no_entries():
    r = run_js(
        CLICKS
        + """
        act(); click("settings"); click("analytics");
        const before = entries.length;
        // Reload: the script starts over, the session history stays.
        historyGuarded = false; shown = null;
        restoreTabFromHash(); act();
        console.log(JSON.stringify([shown, entries.length === before, at]));
        """
    )
    assert r == ["analytics", True, 3]


@needs_node
def test_a_hash_that_corrects_itself_replaces_the_entry_and_keeps_its_marker():
    r = run_js(
        CLICKS
        + """
        act(); pushPage("#meetings"); restoreTabFromHash();
        console.log(JSON.stringify([window.location.hash, history.state, entries.length]));
        """
    )
    assert r == ["#recordings", {"nv": "page"}, 3]


@needs_node
def test_a_recording_that_is_gone_leaves_the_plain_tab():
    r = run_js(
        CLICKS
        + """
        act(); pushPage("#recordings/gone"); restoreTabFromHash().then(() => {
          console.log(JSON.stringify([window.location.hash, shown]));
        });
        """
    )
    assert r == ["#recordings", "recordings"]


@needs_node
def test_a_recording_is_looked_up_once_and_the_tab_is_not_opened_twice():
    r = run_js(
        CLICKS
        + """
        act(); pushPage("#recordings/7"); activations.length = 0;
        restoreTabFromHash().then(() => console.log(JSON.stringify([lookups, activations])));
        """
    )
    # showRecording opens the tab itself; restoring must not open it first.
    assert r == [1, []]


@needs_node
def test_a_lookup_that_ends_after_the_user_moved_on_changes_nothing():
    r = run_js(
        CLICKS
        + """
        act(); pushPage("#recordings/gone");
        const pending = restoreTabFromHash();
        back();                                      // Home again before the lookup answers
        pending.then(() => console.log(JSON.stringify([window.location.hash, shown, entries.map((e) => e.hash)])));
        """
    )
    assert r == ["", "home", ["", "", "#recordings/gone"]]


@needs_node
def test_an_unreadable_recording_id_does_not_throw_and_leaves_the_plain_tab():
    r = run_js(
        CLICKS
        + """
        act(); pushPage("#recordings/%E0%A4%A");
        restoreTabFromHash().then(() => console.log(JSON.stringify([lookups, window.location.hash, shown])));
        """
    )
    assert r == [0, "#recordings", "recordings"]


@needs_node
def test_the_bare_settings_address_shows_the_first_page_and_is_that_page():
    r = run_js(
        CLICKS
        + """
        act(); settingsPage = "models"; pushPage("#settings"); restoreTabFromHash();
        const bare = settingsPage;
        const before = entries.length;
        click("settings");                             // the page that is shown already: no second entry
        pushPage("#settings/dictation"); pushPage("#analytics/overview"); pushPage("#analytics");
        console.log(JSON.stringify([bare, entries.length - before]));
        """
    )
    # "#settings" shows Dictation (not the Models page left open); only "#analytics/overview" -> "#analytics" is a step.
    assert r == ["dictation", 1]


@needs_node
def test_the_guard_waits_for_the_page_start_and_then_keeps_the_address_the_user_sees():
    r = run_js(
        """
        entries[0].hash = "#recordings/gone";
        guardHistory();                                // before the start has settled: nothing is added
        const early = [entries.length, history.state];
        restoreTabFromHash().then(() => {
          markHistoryReady(); guardHistory();
          console.log(JSON.stringify([early, entries.map((e) => e.hash)]));
        });
        """
    )
    assert r[0] == [1, None]
    # The marker and its copy both carry the corrected address.
    assert r[1] == ["#recordings", "#recordings"]


def test_the_back_button_of_a_mouse_and_alt_left_do_not_arm_the_guard():
    init = (STATIC / "dashboard-init.js").read_text(encoding="utf-8")
    assert "if (e.button === 0) guardHistory()" in init
    assert "if (!e.altKey) guardHistory()" in init
    assert "markHistoryReady" in init
