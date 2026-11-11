"""Regression tests for the review, edit, meeting, analytics, home and upload scripts, run under Node with the
stand-in DOM of tests/fake_dom.js: local timestamps, no discarded corrections, no duplicate cards, stale answers
ignored, and failures shown."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_js import STATIC

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

FAKE_DOM = Path(__file__).with_name("fake_dom.js").read_text(encoding="utf-8")
OUT = "const out = (v) => console.log(JSON.stringify(v));\n"


def _source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _fn(source: str, name: str, is_async: bool = False) -> str:
    prefix = "async function" if is_async else "function"
    match = re.search(rf"^{prefix} {name}\(.*?^}}\n", source, re.S | re.M)
    assert match, f"{name} not found"
    return match.group(0)


def run(script: str, tz: str = "UTC"):
    out = subprocess.run(
        ["node", "-"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env={**os.environ, "TZ": tz},
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# -- timestamps ---------------------------------------------------------------------------------------


def test_naive_utc_timestamps_are_read_as_utc_so_they_show_local_time():
    script = (
        OUT
        + _fn(_source("dashboard-core.js"), "parseServerTime")
        + """
    out([parseServerTime("2026-11-06T23:30:00").toISOString(),
         parseServerTime("2026-11-06T23:30:00Z").toISOString(),
         parseServerTime("2026-11-06T23:30:00+01:00").toISOString(),
         parseServerTime("2026-11-06T23:30:00").getDate()]);
    """
    )
    # 23:30 UTC is already the next day in Tokyo: it shows there, not on the UTC day.
    result = run(script, tz="Asia/Tokyo")
    assert result == ["2026-11-06T23:30:00.000Z", "2026-11-06T23:30:00.000Z", "2026-11-06T22:30:00.000Z", 7]


# -- Edit tab -----------------------------------------------------------------------------------------


def test_a_failed_save_of_a_correction_is_shown_as_failed():
    script = (
        FAKE_DOM
        + _fn(_source("dashboard-edit.js"), "saveCorrection", True)
        + """
    (async () => {
      page([["card"]]);
      const card = document.getElementById("card");
      const area = document.createElement("textarea"); area.className = "flagged-textarea"; area.value = "x";
      const btn = document.createElement("button"); btn.closest = () => card;
      card.append(area, btn);
      replies.set("/api/flagged/5/correct", { ok: false });
      await saveCorrection(5, btn);
      const failed = [btn.textContent, btn.disabled];
      replies.set("/api/flagged/5/correct", { ok: true });
      await saveCorrection(5, btn);
      out([failed, btn.textContent]);
    })();
    """
    )
    assert run(script) == [["Error", False], "Saved"]


# -- Review tab ---------------------------------------------------------------------------------------

REVIEW_STUBS = """
page([["review-list"], ["review-progress"], ["review-load-more", "button"], ["tab-review"], ["review-suggestions"]]);
document.getElementById("tab-review").classList.add("active");
const formatDuration = (ms) => ms + "ms";
const parseServerTime = (v) => new Date(v + "Z");
const addDictionaryEntry = async () => true;
globalThis.Audio = function (src) {
  this.src = src; this.paused = false; this.pause = () => { this.paused = true; }; this.play = () => Promise.resolve();
};
"""


def _review(body: str):
    return run(
        FAKE_DOM.replace("const fetch =", "var fetch =")
        + REVIEW_STUBS
        + _source("dashboard-review.js")
        + "\n(async () => {"
        + body
        + "})();"
    )


def test_overlapping_loads_never_show_a_card_twice():
    result = _review(
        """
      const items = [{ id: 9, text: "a", created_at: "2026-11-06T10:00:00" }];
      let release;
      fetch = () => new Promise((resolve) => {
        release = () => resolve({ json: async () => ({ items, remaining: 0 }) });
      });
      const first = loadMoreReviewTranscriptions();
      const second = loadMoreReviewTranscriptions();   // a double click on 'Load more'
      release(); await first; await second;
      out((document.getElementById("review-list")._html.match(/data-id="9"/g) || []).length);
    """
    )
    assert result == 1


def test_reloading_the_review_tab_stops_the_audio():
    result = _review(
        """
      fetch = async () => ({ json: async () => ({ items: [], remaining: 0 }) });
      const btn = document.createElement("button"); btn.className = "flagged-play-btn";
      const card = document.createElement("div"); card.dataset.id = "3"; card.append(btn);
      toggleReviewAudio(card);
      const audio = reviewAudio.audio;
      await loadReviewTranscriptions();
      out([audio.paused, reviewAudio]);
    """
    )
    assert result == [True, None]


def test_enter_on_a_button_is_left_to_the_button_and_a_typed_correction_is_submitted_as_typed():
    result = _review(
        """
      const list = document.getElementById("review-list");
      const card = document.createElement("div"); card.className = "flagged-card"; card.dataset.id = "4";
      const cb = document.createElement("input"); cb.className = "review-correct-cb"; cb.checked = false;
      const area = document.createElement("textarea"); area.className = "review-correction"; area.value = "my fix";
      const submit = document.createElement("button"); submit.className = "flagged-save-btn";
      const play = document.createElement("button"); play.className = "flagged-play-btn";
      card.append(cb, area, play, submit); list.append(card);
      const posted = [];
      fetch = async (url, opts) => {
        if (opts) posted.push(JSON.parse(opts.body));   // the reload after the card fades away has no body
        return { json: async () => ({ ok: true, items: [], remaining: 0 }) };
      };
      const press = (target) => {
        const ev = { key: "Enter", target, preventDefault() {}, ctrlKey: false, altKey: false, metaKey: false };
        docListeners.keydown.forEach((fn) => fn(ev));
      };
      const onButton = { matches: () => false, closest: (s) => (s === "button" ? {} : null) };
      press(onButton);                  // Enter on Play, Retry or Load more: the button handles it itself
      await new Promise((r) => setTimeout(r, 0));
      const afterButton = [posted.length, area.value, cb.checked];
      press({ matches: () => false, closest: () => null });   // Enter elsewhere: the typed text is sent
      await new Promise((r) => setTimeout(r, 0));
      out([afterButton, posted]);
    """
    )
    assert result == [[0, "my fix", False], [{"is_correct": False, "corrected_text": "my fix"}]]


# -- Meetings -----------------------------------------------------------------------------------------


def _meetings(body: str):
    source = _source("dashboard-meetings.js")
    fns = "\n".join(
        [_fn(source, "startMeeting", True), _fn(source, "stopMeeting", True), _fn(source, "trackMeetingJob")]
    )
    stubs = """
    const meetingToggleBtn = { disabled: false };
    const meetingProgress = { style: { display: "none" } };
    const meetingProgressBar = { value: 0 };
    const messages = [];
    const showMeetingStatus = (text, state) => messages.push([text, state]);
    const refreshMeetingStatus = async () => {};
    const loadFileTranscriptions = () => {};
    let recSelectedId = null;
    let meetingJobTimer = null;
    // Timers by hand: setInterval returns an id and ticks run only when a test says so.
    const timers = new Map(); let nextTimer = 1;
    const setInterval = (fn) => { const id = nextTimer++; timers.set(id, fn); return id; };
    const clearInterval = (id) => { timers.delete(id); };
    """
    return run(OUT + stubs + fns + "\n(async () => {" + body + "})();")


def test_a_network_failure_on_start_or_stop_is_shown():
    result = _meetings(
        """
      globalThis.fetch = async () => { throw new Error("offline"); };
      await startMeeting();
      await stopMeeting();
      out([messages.map((m) => m[0]).filter(Boolean), meetingToggleBtn.disabled]);
    """
    )
    assert result == [["✕ The meeting did not start: offline", "✕ The meeting did not stop: offline"], False]


def test_an_older_jobs_late_poll_does_not_end_a_newer_jobs_progress():
    result = _meetings(
        """
      let releaseOld;
      globalThis.fetch = (url) => url.endsWith("/old")
        ? new Promise((resolve) => {
            releaseOld = () => resolve({ ok: true, json: async () => ({ state: "done", result: { id: 1 } }) });
          })
        : Promise.resolve({ ok: true, json: async () => ({ state: "running", percent: 0.5, message: "Working" }) });
      trackMeetingJob("old");             // its first poll is still in flight
      trackMeetingJob("new");             // a newer job starts meanwhile
      await new Promise((r) => setTimeout(r, 0));
      releaseOld();                       // the old poll answers late with 'done'
      await new Promise((r) => setTimeout(r, 0));
      out([meetingProgress.style.display, timers.size, messages.map((m) => m[0]).includes("● Meeting transcribed.")]);
    """
    )
    assert result == ["", 1, False]


# -- Analytics ----------------------------------------------------------------------------------------


def test_a_slower_weekly_answer_does_not_overwrite_monthly():
    source = _source("dashboard-analytics.js")
    script = (
        OUT
        + """
    let currentPeriod = "week"; let timelineLoadSeq = 0; const rendered = [];
    const renderTimeline = (t) => rendered.push(t); const renderLatency = () => {}; const renderAccuracy = () => {};
    const releases = {};
    const api = (url) => {
      const period = url.split("period=")[1];
      return new Promise((resolve) => {
        if (url.includes("over-time")) releases[period] = () => resolve(period); else resolve(null);
      });
    };
    """
        + _fn(source, "loadTimeline", True)
        + """
    (async () => {
      const weekly = loadTimeline();
      currentPeriod = "month";
      const monthly = loadTimeline();
      await new Promise((r) => setTimeout(r, 0));
      releases.month(); await monthly;
      releases.week(); await weekly;
      out(rendered);
    })();
    """
    )
    assert run(script) == ["month"]


# -- Upload -------------------------------------------------------------------------------------------


def test_the_meeting_transcription_started_by_stop_is_not_adopted_as_an_upload():
    source = _source("dashboard-upload.js")
    script = (
        OUT
        + """
    let uploadRunning = false; const calls = [];
    const uploadErrorEl = { style: {} }, uploadNoteEl = { style: {} };
    const fetchJobJson = async () => ({
      ok: true, body: { job: { job_id: "j", filename: "meeting-20261106-100000", state: "running" } },
    });
    const showUploadProgress = () => calls.push("progress");
    const trackUploadJob = async () => calls.push("track");
    const showUploadOutcome = () => calls.push("outcome");
    """
        + _fn(source, "resumeUploadJob", True)
        + "(async () => { await resumeUploadJob(); out(calls); })();"
    )
    assert run(script) == []


def test_the_export_blob_url_is_revoked_after_a_delay():
    source = _source("dashboard-upload.js")
    script = (
        OUT
        + """
    const events = []; const hideFeedback = () => {}; const showFeedback = () => {};
    const URL = { createObjectURL: () => "blob:x", revokeObjectURL: () => events.push("revoked") };
    const document = { createElement: () => ({ click: () => events.push("clicked") }) };
    const fetch = async () => ({ ok: true, headers: { get: () => "" }, blob: async () => ({}) });
    const pending = [];
    globalThis.setTimeout = (fn, ms) => { pending.push([fn, ms]); };
    """
        + _fn(source, "downloadExport", True)
        + "(async () => { await downloadExport('/x', {}); out([events, pending.length, pending[0][1] >= 1000]); })();"
    )
    assert run(script) == [["clicked"], 1, True]
