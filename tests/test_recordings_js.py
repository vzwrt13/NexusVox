"""The Recordings tab under Node with a small stand-in DOM: the list, the upload dialog and the detail pane's tabs,
extension tabs included (registering, switching, a redraw keeping the chosen tab and its content, no extension)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_js import STATIC, dashboard_source

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

# A stand-in DOM (tests/fake_dom.js) and a few page helpers, loaded first into every script.
FAKE_DOM = (Path(__file__).with_name("fake_dom.js")).read_text(encoding="utf-8")

# Helpers the Recordings script takes from earlier files; the pending-state helpers are the real ones.
RECORDINGS_STUBS = r"""
page([["rec-list", "ul"], ["rec-detail", "section"], ["rec-search", "input"], ["rec-options"],
  ["rec-options-toggle", "button"], ["rec-detail-empty", "p"], ["tab-recordings", "section"], ["rec-layout"],
  ["rec-list-pane", "section"], ["rec-resizer"]]);
// localStorage, filled from STORAGE_PRESET (a test's prelude) before the script reads the saved list width.
const storage = new Map(globalThis.STORAGE_PRESET || []);
globalThis.localStorage = { getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)), removeItem: (k) => storage.delete(k) };
// The list pane is as wide as the layout's --rec-list-width, clamped as recordings.css does, else 340px.
document.getElementById("rec-layout").width = 1000;
document.getElementById("rec-list-pane").getBoundingClientRect = () => {
  const layout = document.getElementById("rec-layout");
  const set = parseFloat(layout.style.getPropertyValue("--rec-list-width")) || 340;
  return { width: Math.min(Math.max(set, 240), Math.max(240, layout.width / 2)) };
};
let extensionExports = [{ id: "txt", label: "Text" }];
let extensionUploadActions = [];
const runningUploadActions = new Set();
const extensionPanels = {};
const formatClock = (s) => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
const setStatus = () => {};
for (const f of ["openExtensionPanel", "loadPendingMeetings", "openMeetingRecording", "refreshDiarizeToggle",
  "resumeUploadJob", "activateTab", "downloadExport", "runUploadAction"]) globalThis[f] = () => {};
const loadLlmStatus = () => Promise.resolve();
const pushed = [];
const pushPage = (hash) => pushed.push(hash);
const replaceHash = (hash) => pushed.push("replace " + hash);
const window = { location: { hash: "" } };
"""

ROWS = [
    {
        "id": 7,
        "kind": "meeting",
        "title": "Weekly sync",
        "original_filename": "meeting-1",
        "text": "Speaker 1: Let us move the beta.\n\nSpeaker 2: Works for me.",
        "duration_ms": 2520000,
        "created_at": "2026-11-06T14:30:00",
        "has_audio": True,
        "language": "en",
    },
    {
        "id": 3,
        "kind": "upload",
        "title": None,
        "original_filename": "pricing-memo.wav",
        "text": "A short memo about pricing.",
        "duration_ms": 360000,
        "created_at": "2026-10-30T09:00:00",
        "has_audio": False,
        "language": "en",
    },
]


def _node(script: str) -> subprocess.CompletedProcess:
    # Through stdin: the script is longer than a Windows command line may be.
    return subprocess.run(["node", "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=30)


def _section(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end)]


def run_recordings(body: str, prelude: str = ""):
    source = dashboard_source()
    pending = _section(source, "// ── Pending and failed states", "// ── end of pending and failed states")
    recordings = (STATIC / "dashboard-recordings.js").read_text(encoding="utf-8")
    core = _section(
        (STATIC / "dashboard-core.js").read_text(encoding="utf-8"),
        "function parseServerTime",
        "function formatDuration",
    )
    parts = [prelude, FAKE_DOM, RECORDINGS_STUBS, pending, core, recordings, f"const ROWS = {json.dumps(ROWS)};", body]
    script = "\n".join(parts)
    out = _node(script)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A detail pane as buildRecordingDetail lays it out, built from elements so the tab row can be walked.
DETAIL = r"""
function makeDetail(item) {
  const detail = document.createElement("article");
  detail.className = "rec-detail";
  detail.dataset.id = String(item.id);
  detail._item = item;
  detail._lines = transcriptLines(item.text, [], item.kind);
  detail._textKey = recordingTextKey(item);
  const tabs = document.createElement("div"); tabs.className = "rec-tabs";
  const panels = document.createElement("div"); panels.className = "rec-panels";
  detail.append(tabs, panels);
  document.getElementById("rec-detail").replaceChildren(detail);
  renderRecordingTabs(detail, item);
  return detail;
}
const NexusVoxExt_register = (id, tab) => addRecordingTab(id, tab);
// A redraw through the real path: the list was loaded again and the same recording is still chosen. The head is
// left out (it is markup this stand-in DOM does not parse), as it is while its title is being edited.
function renderSelectedRecordingFor(d) {
  recItems = ROWS;
  recSelectedId = d.dataset.id;
  d._editing = true;
  renderSelectedRecording(false);
  d._editing = false;
}
const tabNames = (d) => d.querySelectorAll(".rec-tab").map((b) => b.textContent);
const chosen = (d) => d.querySelector('.rec-tab[aria-selected="true"]').dataset.tab;
const panelKind = (d) => d.querySelector(".rec-panel").children[0].className;
const listHtml = () => document.getElementById("rec-list").children.map((li) => li._html || li.innerHTML).join("");
const LIST_URL = "/api/file-transcriptions?limit=100";
"""


# -- the list -------------------------------------------------------------------------------------


def test_the_list_holds_meetings_and_uploads_and_marks_the_uploads():
    result = run_recordings(
        DETAIL
        + """recItems = ROWS; recSelectedId = "7"; renderRecordingList();
        const html = listHtml();
        out([(html.match(/data-kind="(\\w+)"/g) || []), html.includes("Weekly sync"), html.includes("pricing-memo.wav"),
          recordingListMeta(ROWS[0]).includes("Upload"), recordingListMeta(ROWS[1]).endsWith("6 min · Upload")]);"""
    )
    assert result == [['data-kind="meeting"', 'data-kind="upload"'], True, True, False, True]


def test_a_refresh_keeps_the_row_elements_so_focus_and_place_survive():
    result = run_recordings(
        DETAIL
        + """recItems = ROWS; renderRecordingList();
        const list = document.getElementById("rec-list");
        const [first, second] = list.children;
        renderRecordingList();                                   // a poll with nothing changed
        const unchanged = list.children[0] === first && list.children[1] === second;
        recItems = [{ ...ROWS[0], title: "Renamed" }, ROWS[1]];  // one row changed
        renderRecordingList();
        const sameElement = list.children[0] === first && first._html.includes("Renamed");
        recItems = [ROWS[1], ROWS[0]];                           // the order changed
        renderRecordingList();
        const moved = list.children[0] === second && list.children[1] === first;
        recItems = [ROWS[1]];                                    // a row is gone
        renderRecordingList();
        out([unchanged, sameElement, moved, list.children.length, list.children[0] === second]);"""
    )
    assert result == [True, True, True, 1, True]


def test_search_finds_titles_and_transcript_text_and_says_when_nothing_matches():
    result = run_recordings(
        """recItems = ROWS; const search = document.getElementById("rec-search");
        const found = (q) => { search.value = q; return shownRecordings().map((i) => i.id); };
        const counts = [found(""), found("WEEKLY"), found("pricing"), found("works for me"), found("nothing like it")];
        renderRecordingList();
        out([counts, document.getElementById("rec-list").innerHTML.includes("Nothing matches")]);"""
    )
    assert result == [[[7, 3], [7], [3], [7], []], True]


def test_the_chosen_recording_stays_while_the_search_hides_it():
    result = run_recordings(
        DETAIL
        + """recItems = ROWS; recSelectedId = "7";
        const d = makeDetail(ROWS[0]);
        d._editing = true;  // the head is markup this stand-in DOM does not parse
        document.getElementById("rec-search").value = "pricing";   // matches only the upload
        renderRecordingList(); renderSelectedRecording(false);
        const kept = [currentDetail() === d, recSelectedId];
        document.getElementById("rec-search").value = "";
        renderRecordingList(); renderSelectedRecording(false);
        out([...kept, currentDetail() === d, recSelectedId]);"""
    )
    assert result == [True, "7", True, "7"]


def test_lengths_read_like_the_design():
    result = run_recordings("out([45000, 360000, 2520000, 4320000].map(formatRecordingLength));")
    assert result == ["45 s", "6 min", "42 min", "1 h 12 min"]


# -- the transcript ---------------------------------------------------------------------------------


def test_speaker_paragraphs_become_timed_lines_when_the_turns_line_up():
    result = run_recordings(
        """const turns = [{speaker: 1, start: 751.2, end: 760}, {speaker: 2, start: 764, end: 770}];
        out([transcriptLines(ROWS[0].text, turns, "upload"),
          transcriptLines(ROWS[0].text, turns.slice(0, 1), "upload").map((l) => l.start),
          transcriptLines("Note: one paragraph only.", [], "upload")]);"""
    )
    timed, mismatched, plain = result
    assert timed == [
        {"who": "Speaker 1", "colour": 0, "text": "Let us move the beta.", "start": 751.2, "end": 760},
        {"who": "Speaker 2", "colour": 1, "text": "Works for me.", "start": 764, "end": 770},
    ]
    assert mismatched == [None, None]  # two paragraphs, one turn: no times rather than wrong ones
    assert plain == [{"who": None, "colour": None, "text": "Note: one paragraph only.", "start": None, "end": None}]


# -- the detail pane's tabs and the extension hook ---------------------------------------------------


def test_without_extensions_the_transcript_shows_and_summary_is_a_pro_note():
    result = run_recordings(
        DETAIL
        + """const d = makeDetail(ROWS[0]);
        const summary = d.querySelector('.rec-tab[data-tab="summary"]');
        const before = [tabNames(d), chosen(d), panelKind(d), recordingTabs.length];
        summary.click();
        const note = d.querySelector(".rec-panel").children[0];
        out([...before, chosen(d), panelKind(d), note.innerHTML.includes("nexusvox.app/pro")]);"""
    )
    assert result == [
        ["SummaryPro", "Transcript", "Speakers"],
        "transcript",
        "rec-transcript",
        0,
        "summary",
        "rec-pro-note",
        True,
    ]


def test_an_extension_tab_is_registered_switched_to_and_kept_across_redraws():
    result = run_recordings(
        DETAIL
        + """const rendered = [];
        const d = makeDetail(ROWS[0]);
        const render = (c, rec) => { rendered.push(rec.id); c.textContent = "Notes of " + rec.id; };
        const ok = NexusVoxExt_register("notes", { title: "Notes", render });
        const names = tabNames(d);
        const lazy = rendered.length;                      // nothing drawn until the tab is opened
        d.querySelector('.rec-tab[data-tab="notes"]').click();
        const kept = d.querySelector(".rec-ext-tab");
        renderSelectedRecordingFor(d);                     // a redraw of the same recording
        const afterRedraw = [chosen(d), d.querySelector(".rec-ext-tab") === kept, kept.textContent, rendered.length];
        const other = makeDetail(ROWS[1]);                 // another recording: same tab, its own content
        const otherText = other.querySelector(".rec-ext-tab").textContent;
        const back = makeDetail(ROWS[0]);                  // back again: the first content, not drawn again
        const same = back.querySelector(".rec-ext-tab") === kept;
        out([ok, names, lazy, afterRedraw, chosen(other), otherText, same, rendered]);""",
    )
    assert result == [
        True,
        ["SummaryPro", "Transcript", "Speakers", "Notes"],
        0,
        ["notes", True, "Notes of 7", 1],
        "notes",
        "Notes of 3",
        True,
        [7, 3],
    ]


def test_a_summary_tab_takes_the_pro_notes_place_and_can_be_drawn_again():
    result = run_recordings(
        DETAIL
        + """let draws = 0;
        const render = (c) => { draws += 1; c.textContent = "draw " + draws; };
        NexusVoxExt_register("summary", { title: "Summary", render });
        const d = makeDetail(ROWS[0]);
        chooseRecordingTab("summary");
        const first = d.querySelector(".rec-ext-tab").textContent;
        refreshRecordingTabContent("summary", 7);           // e.g. a summary was just generated
        out([tabNames(d), first, d.querySelector(".rec-ext-tab").textContent, draws]);"""
    )
    assert result == [["Summary", "Transcript", "Speakers"], "draw 1", "draw 2", 2]


def test_the_hook_refuses_core_and_malformed_ids_and_hides_unavailable_tabs():
    result = run_recordings(
        DETAIL
        + """const r = (id, tab) => NexusVoxExt_register(id, tab);
        const render = () => {};
        const meetingsOnly = { title: "Meeting notes", render, available: (rec) => rec.kind === "meeting" };
        const answers = [r("transcript", { render }), r("Bad Id", { render }), r("x", {}),
          r("only-meetings", meetingsOnly), r("only-meetings", { render })];
        out([answers, tabNames(makeDetail(ROWS[0])).length, tabNames(makeDetail(ROWS[1])).length,
          chooseRecordingTab("only-meetings"), chooseRecordingTab("nope")]);"""
    )
    # On the upload the tab does not exist, so it cannot be chosen there.
    assert result == [[False, False, False, True, False], 4, 3, False, False]


def test_labels_need_speaker_timing_or_a_meeting():
    result = run_recordings(
        """const notes = "Agenda: the release.\\n\\nNote: ask the pilot users.";
        const who = (lines) => lines.map((l) => l.who);
        out([who(transcriptLines(notes, [], "upload")), who(transcriptLines(notes, [], "meeting")),
          who(transcriptLines(notes, [{start: 0, end: 1}], "upload"))]);"""
    )
    # A plain upload with "Agenda:"/"Note:" paragraphs has no speakers; a meeting, or speaker timing, has them.
    assert result == [[None, None], ["Agenda", "Note"], ["Agenda", "Note"]]


def test_turns_are_fetched_again_for_new_text_and_after_a_failure():
    result = run_recordings(
        """replies.set("/api/file-transcriptions/7/turns", { turns: [{ speaker: 1, start: 0, end: 1 }] });
        replies.set("/api/file-transcriptions/3/turns", { status: 500, body: {} });
        const asked = () => fetched.filter((f) => f[0].endsWith("/turns")).map((f) => f[0].split("/")[3]);
        (async () => {
          await loadRecordingTurns(ROWS[0]);
          await loadRecordingTurns(ROWS[0]);                             // cached
          await loadRecordingTurns({ ...ROWS[0], text: "Speaker 1: Rewritten by an action." });
          const failed = await loadRecordingTurns(ROWS[1]);
          await loadRecordingTurns(ROWS[1]);                             // a failure is not kept
          out([asked(), failed]);
        })();"""
    )
    assert result == [["7", "7", "3", "3"], []]


def test_what_is_kept_per_recording_goes_with_the_recording_and_stays_bounded():
    result = run_recordings(
        DETAIL
        + """recTabContent.set("notes:99", {}); recTabContent.set("notes:7", {});
        recTurns.set("99:1:x", Promise.resolve([])); recShowOriginal.add("99"); recShowOriginal.add("7");
        pruneRecordingCaches(ROWS);
        const pruned = [[...recTabContent.keys()], [...recTurns.keys()], [...recShowOriginal]];
        recTabContent.clear();
        const tab = { id: "notes", title: "Notes", render: () => {} };
        for (let id = 1; id <= 40; id += 1) recordingTabContainer(tab, { id });
        const bounded = recTabContent.size <= REC_TAB_KEEP;
        out([pruned, bounded, recTabContent.has("notes:40"), recTabContent.has("notes:1")]);"""
    )
    assert result == [[["notes:7"], [], ["7"]], True, True, False]


def test_show_transcript_switches_to_the_transcript_tab_and_marks_the_passage():
    result = run_recordings(
        DETAIL
        + """replies.set(LIST_URL, ROWS);
        const opened = []; activateTab = (...args) => opened.push(args[0]);
        NexusVoxExt_register("notes", { title: "Notes", render: () => {} });
        recChosenTab = "notes";
        const d = makeDetail(ROWS[0]);
        d._editing = true;
        d._lines = transcriptLines(ROWS[0].text, [{start: 750, end: 760}, {start: 764, end: 770}], "meeting");
        showRecording(7, { mark: { start: 765, end: 766 } }).then((found) => {
          out([found, recChosenTab, recMark.start, recMark.id, opened, recItems.length, pushed]);
        });"""
    )
    # Opening a recording from elsewhere is a history step of its own.
    assert result == [True, "transcript", 765, "7", ["recordings"], 2, ["#recordings/7"]]


def test_a_recording_restored_from_history_adds_no_step_and_choosing_another_one_renames_the_address():
    result = run_recordings(
        DETAIL
        + """replies.set(LIST_URL, ROWS);
        const opened = []; activateTab = (...args) => opened.push(args[0]);
        const d = makeDetail(ROWS[0]);
        d._editing = true; // a pane being edited is kept, so no detail has to be drawn
        showRecording(7, { restore: true }).then((found) => {
          window.location.hash = "#recordings/7";
          renderSelectedRecording = () => {}; // the pane is not under test here
          selectRecording(3);
          out([found, recSelectedId, pushed]);
        });"""
    )
    assert result == [True, "3", ["replace #recordings/3"]]


def test_coming_back_to_the_chosen_recording_keeps_its_tab_and_passage():
    result = run_recordings(
        DETAIL
        + """replies.set(LIST_URL, ROWS);
        const opened = []; activateTab = (...args) => opened.push(args[0]);
        const d = makeDetail(ROWS[0]);
        d._editing = true;
        recSelectedId = "7"; recChosenTab = "summary"; recMark = { id: "7", start: 1, end: 2, text: "x" };
        showRecording(7, { restore: true }).then((found) => {
          out([found, recChosenTab, recMark.start, opened]);
        });"""
    )
    assert result == [True, "summary", 1, ["recordings"]]


def test_a_recording_looked_up_for_a_step_that_was_left_meanwhile_changes_nothing():
    result = run_recordings(
        DETAIL
        + """replies.set(LIST_URL, ROWS);
        const opened = []; activateTab = (...args) => opened.push(args[0]);
        recChosenTab = "summary";
        showRecording(7, { restore: true, current: () => false }).then((found) => {
          out([found, recChosenTab, recSelectedId, opened, recItems.length]);
        });"""
    )
    assert result == [False, "summary", None, [], 0]


def test_show_transcript_of_an_unknown_recording_leaves_the_page_alone():
    result = run_recordings(
        DETAIL
        + """replies.set(LIST_URL, ROWS);
        const opened = []; activateTab = (...args) => opened.push(args[0]);
        pushed.length = 0;
        recChosenTab = "speakers";
        showRecording(42, { mark: { text: "beta" } }).then((found) => {
          out([found, opened, pushed.length, recChosenTab, recMark, recItems.length]);
        });"""
    )
    assert result == [False, [], 0, "speakers", None, 0]


def test_recording_tab_scripts_load_once_without_the_panels_main_script():
    result = run_recordings(
        """extensionPanels.pro = { script: "/ext/pro/panel.js", recordingScript: "/ext/pro/recording.js" };
        extensionPanels.plain = { script: "/ext/plain/panel.js", recordingScript: null };
        const opened = []; openExtensionPanel = (id) => opened.push(id);
        loadRecordingTabScripts();
        loadRecordingTabScripts();                                     // the tab opened again
        out([document.head.children.map((s) => s.src), opened]);"""
    )
    assert result == [["/ext/pro/recording.js"], []]


# -- the upload dialog --------------------------------------------------------------------------------


def run_upload(body: str):
    source = dashboard_source()
    pending = _section(source, "// ── Pending and failed states", "// ── end of pending and failed states")
    upload = (STATIC / "dashboard-upload.js").read_text(encoding="utf-8")
    upload = upload[: upload.index("// ── Pending and failed states")]  # the helpers come in once, below
    ids = [
        "upload-zone",
        ["upload-input", "input"],
        ["upload-browse", "button"],
        "upload-selected",
        "upload-selected-name",
        "upload-selected-meta",
        ["upload-remove", "button"],
        "upload-zone-text",
        "upload-zone-ready",
        ["upload-transcribe", "button"],
        ["upload-dialog", "dialog"],
        ["tab-recordings", "section"],
        "rec-drop-overlay",
        ["upload-browse-again", "button"],
        ["upload-open", "button"],
        ["upload-cleanup-toggle", "input"],
        ["upload-cleanup-level", "select"],
        ["upload-diarize-toggle", "input"],
        "upload-diarize-hint",
        "upload-status",
        "upload-status-text",
        "upload-progress",
        ["upload-progress-bar", "progress"],
        "upload-error",
        "upload-note",
    ]
    stubs = f"""
    page({json.dumps([i if isinstance(i, list) else [i] for i in ids])});
    const URL = {{ createObjectURL: () => "blob:x", revokeObjectURL() {{}} }};
    class Audio {{ addEventListener() {{}} removeAttribute() {{}} load() {{}} }};
    const setInterval = () => 0;
    // The duration probe's 10 s timeout must not keep Node running; a zero delay still runs.
    const nodeSetTimeout = globalThis.setTimeout;
    const setTimeout = (fn, ms) => (ms ? 0 : nodeSetTimeout(fn, 0));
    const clearTimeout = () => {{}};
    const refreshLlmHints = () => {{}};
    let llmLoads = 0;
    const loadLlmStatus = () => {{ llmLoads += 1; return Promise.resolve(); }};
    const FormData = class {{ append() {{}} }};
    let extensionUploadActions = [];
    const uploadActionHandlers = {{}};
    const file = {{ name: "call.wav", size: 2048 }};
    const files = (list) => ({{ dataTransfer: {{ types: ["Files"], files: list }} }});
    """
    script = "\n".join([FAKE_DOM, stubs, upload, pending, body])
    out = _node(script)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_upload_button_opens_the_dialog_with_fresh_options():
    result = run_upload(
        """const dialog = document.getElementById("upload-dialog");
        document.getElementById("upload-open").click();
        out([dialog.open, llmLoads, fetched.map((f) => f[0])]);"""
    )
    assert result == [True, 1, ["/api/models/current"]]


def test_a_file_dropped_on_the_tab_opens_the_dialog_with_it():
    result = run_upload(
        """const tab = document.getElementById("tab-recordings"), overlay = document.getElementById("rec-drop-overlay");
        overlay.hidden = true;
        tab.dispatch("dragenter", files([]));
        const shown = !overlay.hidden;
        tab.dispatch("drop", files([file]));
        const el = (id) => document.getElementById(id);
        out([shown, overlay.hidden, el("upload-dialog").open, el("upload-selected-name").textContent,
          el("upload-transcribe").disabled]);"""
    )
    assert result == [True, True, True, "call.wav", False]


def test_a_drop_on_the_dialogs_own_zone_is_taken_once():
    result = run_upload(
        """const dialog = document.getElementById("upload-dialog");
        dialog.append(document.getElementById("upload-zone"));
        document.getElementById("tab-recordings").append(dialog);
        let opened = 0; dialog.showModal = () => { opened += 1; dialog.open = true; };
        document.getElementById("upload-zone").dispatch("drop", files([file]));
        out([opened, document.getElementById("upload-selected-name").textContent]);"""
    )
    assert result == [0, "call.wav"]


def test_transcribe_closes_the_dialog_and_starts_the_job():
    result = run_upload(
        """const dialog = document.getElementById("upload-dialog");
        selectUploadFile(file);
        dialog.showModal();
        document.getElementById("upload-transcribe").click();
        setTimeout(() => out([dialog.open, uploadRunning, fetched.map((f) => f.join(" "))]), 0);"""
    )
    assert result[0] is False and result[1] is True
    assert "/api/file-transcribe POST" in result[2]


def test_a_drop_while_a_file_is_transcribing_says_so_instead():
    result = run_upload(
        """uploadRunning = true;
        document.getElementById("tab-recordings").dispatch("drop", files([file]));
        const error = document.getElementById("upload-error");
        const said = error.textContent.includes("still being transcribed");
        out([document.getElementById("upload-dialog").open, error.style.display, said]);"""
    )
    assert result == [False, "block", True]


# -- the resizable list pane ------------------------------------------------------------------------

RESIZE = r"""
const layout = document.getElementById("rec-layout");
const handle = document.getElementById("rec-resizer");
const widthSet = () => layout.style.getPropertyValue("--rec-list-width");
const key = (k, shiftKey = false) => handle.dispatch("keydown", { key: k, shiftKey });
const pointer = (type, clientX, extra = {}) => handle.dispatch(type, { pointerId: 1, button: 0, clientX, ...extra });
"""


def test_dragging_the_handle_sets_the_width_within_the_limits_and_keeps_it():
    result = run_recordings(
        RESIZE
        + """pointer("pointerdown", 500);
        const resizing = layout.classList.contains("resizing");
        pointer("pointermove", 600);
        const during = [widthSet(), storage.has("recordings-list-width")];  // only the end of a drag is stored
        pointer("pointermove", 2000);                                        // past half the tab
        const wide = widthSet();
        pointer("pointermove", -500);                                        // past the minimum
        pointer("pointerup", -500);
        out([resizing, during, wide, widthSet(), storage.get("recordings-list-width"),
          layout.classList.contains("resizing"), handle.getAttribute("aria-valuenow")]);"""
    )
    assert result == [True, ["440px", False], "500px", "240px", "240", False, "240"]


def test_a_right_click_or_another_pointer_does_not_drag():
    result = run_recordings(
        RESIZE
        + """pointer("pointerdown", 500, { button: 2 });
        pointer("pointermove", 600);
        pointer("pointerdown", 500);
        pointer("pointermove", 700, { pointerId: 2 });
        pointer("pointerup", 700, { pointerId: 2 });
        out([widthSet(), layout.classList.contains("resizing")]);"""
    )
    assert result == ["", True]


def test_the_arrow_keys_move_the_handle_and_home_and_end_go_to_the_limits():
    result = run_recordings(
        RESIZE
        + """const seen = [];
        for (const [k, shift] of [["ArrowRight"], ["ArrowLeft", true], ["End"], ["ArrowRight"], ["Home"],
          ["ArrowLeft"], ["Enter"]]) { key(k, shift); seen.push(widthSet()); }
        handle.dispatch("focus");
        out([seen, storage.get("recordings-list-width"), handle.getAttribute("aria-valuemax")]);"""
    )
    assert result == [["356px", "292px", "500px", "500px", "240px", "240px", "240px"], "240", "500"]


def test_a_double_click_resets_the_width_and_forgets_it():
    result = run_recordings(
        RESIZE
        + """key("ArrowRight");
        const before = [widthSet(), storage.get("recordings-list-width")];
        handle.dispatch("dblclick");
        out([before, widthSet(), storage.has("recordings-list-width"), handle.getAttribute("aria-valuenow")]);"""
    )
    assert result == [["356px", "356"], "", False, "340"]


def test_the_saved_width_is_restored_when_the_page_loads():
    restored = run_recordings(
        RESIZE + "out(widthSet());", prelude='globalThis.STORAGE_PRESET = [["recordings-list-width", "420"]];'
    )
    garbage = run_recordings(
        RESIZE + "out(widthSet());", prelude='globalThis.STORAGE_PRESET = [["recordings-list-width", "wide"]];'
    )
    assert [restored, garbage] == ["420px", ""]


def test_without_storage_the_handle_still_works():
    result = run_recordings(
        RESIZE
        + """globalThis.localStorage = { getItem() { throw new Error("off"); }, setItem() { throw new Error("off"); },
          removeItem() { throw new Error("off"); } };
        key("ArrowRight"); handle.dispatch("dblclick"); key("End");
        out([widthSet(), storedRecListWidth()]);"""
    )
    assert result == ["500px", None]


# -- the Export menu: one row per content, formats as chips ------------------------------------------------

# What the server lists today: the built-in targets and the Pro extension's (ids and labels as nexusvox-pro sets them).
EXPORT_TARGETS = """extensionExports = [
  { id: "txt", label: "Text", extension: ".txt" },
  { id: "srt", label: "Subtitles (SRT)", extension: ".srt" },
  { id: "vtt", label: "Subtitles (VTT)", extension: ".vtt" },
  { id: "pro-transcript", label: "Transcript with speaker names", extension: ".named.txt" },
  { id: "pro-summary-meeting", label: "Summary: Meeting summary (Markdown)", extension: ".summary-meeting.md" },
  { id: "pro-summary-board", label: "Summary: Board minutes (Markdown)", extension: ".summary-board.md" },
  { id: "pro-research-docx", label: "Transcript by speaker (DOCX)", extension: ".speakers.docx" },
  { id: "pro-research-csv", label: "Transcript by speaker (CSV)", extension: ".speakers.csv" },
];
const rowsOf = (item) => exportRows(item).map((r) => [r.label, r.chips.map((c) => c.format)]);
"""


def test_the_export_menu_has_one_row_per_content_with_formats_as_chips():
    result = run_recordings(
        EXPORT_TARGETS + """out(rowsOf({ id: 7, exports_unavailable: { "pro-summary-board": "No summary yet" } }));"""
    )
    assert result == [
        ["Transcript", ["TXT"]],
        ["Subtitles", ["SRT", "VTT"]],
        ["By speaker", ["TXT", "DOCX", "CSV"]],
        ["Meeting summary", ["MD"]],
    ]


def test_summaries_that_do_not_exist_are_left_out_and_every_one_that_exists_gets_a_row():
    result = run_recordings(
        EXPORT_TARGETS
        + """const without = { id: 7, exports_unavailable: { "pro-summary-meeting": "No summary yet",
          "pro-summary-board": "No summary yet" } };
        out([rowsOf(without).map((r) => r[0]), rowsOf({ id: 7 }).map((r) => r[0])]);"""
    )
    assert result == [
        ["Transcript", "Subtitles", "By speaker"],
        ["Transcript", "Subtitles", "By speaker", "Meeting summary", "Board minutes"],
    ]


def test_the_speaker_row_is_left_out_without_speaker_names_and_subtitles_without_word_timing():
    result = run_recordings(
        EXPORT_TARGETS
        + """const noSpeakers = { id: 7, exports_unavailable: { "pro-transcript": "No speaker names",
          "pro-research-docx": "No speakers", "pro-research-csv": "No speakers" } };
        const noTiming = { id: 7, exports_unavailable: { srt: "No timing", vtt: "No timing" } };
        const partly = { id: 7, exports_unavailable: { "pro-research-csv": "No speakers" } };
        out([rowsOf(noSpeakers), rowsOf(noTiming).map((r) => r[0]), rowsOf(partly)[2]]);"""
    )
    assert result[0] == [
        ["Transcript", ["TXT"]],
        ["Subtitles", ["SRT", "VTT"]],
        ["Meeting summary", ["MD"]],
        ["Board minutes", ["MD"]],
    ]
    assert result[1] == ["Transcript", "By speaker", "Meeting summary", "Board minutes"]
    assert result[2] == ["By speaker", ["TXT", "DOCX"]]


def test_a_pending_or_failed_export_is_not_a_row_and_nothing_is_disabled():
    result = run_recordings(
        EXPORT_TARGETS
        + """const html = exportMenuHtml({ id: 7, exports_unavailable: {
          "pro-summary-meeting": { pending: "Summarising" },
          "pro-summary-board": { error: "Offline", retry: "summarise" }, srt: "No timing" } });
        out([html.includes("Meeting summary"), html.includes("Board minutes"), html.includes("disabled"),
          html.includes("No timing"), html.includes("data-format=\\"SRT\\"")]);"""
    )
    assert result == [False, False, False, False, False]


def test_an_export_of_an_unknown_extension_keeps_a_row_of_its_own():
    result = run_recordings(
        """extensionExports = [{ id: "txt", label: "Text", extension: ".txt" },
          { id: "my-csv", label: "Team table (CSV)", extension: ".csv" }];
        out(exportRows({ id: 7 }).map((r) => [r.label, r.chips.map((c) => c.format)]));"""
    )
    assert result == [["Transcript", ["TXT"]], ["Team table", ["CSV"]]]


def test_the_menu_markup_has_icons_hints_chips_and_one_footer_line():
    result = run_recordings(
        EXPORT_TARGETS
        + """extensionUploadActions = [{ id: "summarise", label: "Summarise" }];
        const html = exportMenuHtml({ id: 7, exports_unavailable: { "pro-summary-board": "No summary yet" } });
        const without = (extensionUploadActions = [], exportMenuHtml({ id: 7 }));
        out([(html.match(/rec-export-row"/g) || []).length, (html.match(/class="rec-chip rec-export"/g) || []).length,
          (html.match(/rec-export-ico/g) || []).length, html.includes("Uses your speaker names"),
          html.includes('data-href="/api/file-transcriptions/7/export/pro-research-docx"'),
          html.includes('aria-label="By speaker as DOCX"'),
          html.includes("Other summaries appear here after you create them with Summarise. Files go to your Downloads"),
          without.includes("Summarise"), without.includes("Files go to your Downloads folder.")]);"""
    )
    assert result == [4, 7, 4, True, True, True, True, False, True]


def test_an_empty_menu_says_so_and_without_any_target_it_says_no_formats():
    result = run_recordings(
        """extensionExports = [{ id: "txt", label: "Text", extension: ".txt" }];
        const none = exportMenuHtml({ id: 7, exports_unavailable: { txt: "No text" } });
        extensionExports = [];
        out([none.includes("Nothing can be exported from this recording yet."), exportMenuHtml({ id: 7 })]);"""
    )
    assert result == [True, '<p class="rec-menu-empty">No export formats are available.</p>']


CHIP = """
const rowsEl = document.createElement("div"); rowsEl.className = "rec-export-rows";
const errorEl = document.createElement("p"); errorEl.className = "rec-export-error"; errorEl.hidden = true;
const chip = document.createElement("button"); chip.className = "rec-chip rec-export";
chip.dataset.href = "/api/file-transcriptions/7/export/txt"; chip.dataset.format = "TXT"; chip.textContent = "TXT";
rowsEl.append(chip, errorEl); document.body.append(rowsEl);
const feedback = []; globalThis.showFeedback = (item, kind, text) => feedback.push([kind, text]);
const timers = []; globalThis.setTimeout = (fn) => timers.push(fn);
const detail = { _item: { id: 7 } };
"""


def test_a_clicked_chip_exports_at_once_and_shows_a_mint_check_for_a_moment():
    result = run_recordings(
        CHIP
        + """(async () => {
          const calls = [];
          globalThis.downloadExport = async (url, item, report) => { calls.push([url, item.id, report]); return ""; };
          await runExportChip(chip, detail);
          const shown = [chip.textContent, chip.classList.contains("done")];
          timers[0]();
          out([calls, shown, [chip.textContent, chip.classList.contains("done")], errorEl.hidden]);
        })();"""
    )
    assert result == [
        [["/api/file-transcriptions/7/export/txt", 7, False]],
        ["✓ TXT", True],
        ["TXT", False],
        True,
    ]


def test_a_failed_export_shows_its_error_in_the_menu_and_no_check():
    result = run_recordings(
        CHIP
        + """(async () => {
          globalThis.downloadExport = async () => "No readable word timestamps are stored for this upload";
          await runExportChip(chip, detail);
          out([errorEl.hidden, errorEl.textContent, chip.textContent, chip.classList.contains("done"), timers.length]);
        })();"""
    )
    assert result == [False, "No readable word timestamps are stored for this upload", "TXT", False, 0]


def test_the_next_click_clears_the_error_and_a_click_while_it_runs_is_ignored():
    result = run_recordings(
        CHIP
        + """(async () => {
          let answer = "Export failed (500)"; let calls = 0; let release;
          globalThis.downloadExport = () => {
            calls++;
            return new Promise((resolve) => { release = () => resolve(answer); });
          };
          const first = runExportChip(chip, detail);
          await runExportChip(chip, detail); // while the first one runs
          release(); await first;
          const failed = [errorEl.hidden, calls];
          answer = "";
          const second = runExportChip(chip, detail);
          const cleared = errorEl.hidden;
          release(); await second;
          out([failed, cleared, calls, chip.textContent]);
        })();"""
    )
    assert result == [[False, 1], True, 2, "✓ TXT"]


def test_arrow_keys_move_between_chips_in_the_export_menu_and_escape_closes_it():
    result = run_recordings(
        """const wrap = document.createElement("div"); wrap.className = "rec-menu-wrap";
        const button = document.createElement("button"); button.className = "rec-menu-btn";
        const menu = document.createElement("div"); menu.className = "rec-menu rec-menu-export"; menu.hidden = true;
        const chips = ["TXT", "SRT", "VTT"].map((f) => { const c = document.createElement("button");
          c.className = "rec-chip rec-export"; c.textContent = f; return c; });
        menu.append(...chips); wrap.append(button, menu);
        bindRecMenu(wrap);
        button.click();
        const trail = [document.activeElement === chips[0]];
        const press = (key) => wrap.dispatch("keydown", { key });
        press("ArrowRight"); trail.push(document.activeElement === chips[1]);
        press("ArrowDown"); trail.push(document.activeElement === chips[2]);
        press("ArrowRight"); trail.push(document.activeElement === chips[0]);
        press("ArrowLeft"); trail.push(document.activeElement === chips[2]);
        press("ArrowUp"); trail.push(document.activeElement === chips[1]);
        chips[1].click(); trail.push(menu.hidden); // a chip click leaves the menu open
        press("Escape"); trail.push(menu.hidden, document.activeElement === button);
        out(trail);"""
    )
    assert result == [True, True, True, True, True, True, False, True, True]


def test_left_and_right_do_nothing_in_the_more_menu():
    result = run_recordings(
        """const wrap = document.createElement("div"); wrap.className = "rec-menu-wrap";
        const button = document.createElement("button"); button.className = "rec-menu-btn";
        const menu = document.createElement("div"); menu.className = "rec-menu"; menu.hidden = true;
        const items = ["Rename", "Delete"].map((l) => { const b = document.createElement("button");
          b.className = "rec-menu-item"; b.textContent = l; return b; });
        menu.append(...items); wrap.append(button, menu);
        bindRecMenu(wrap);
        button.click();
        const press = (key) => wrap.dispatch("keydown", { key });
        press("ArrowRight"); const stayed = document.activeElement === items[0];
        press("ArrowDown"); out([stayed, document.activeElement === items[1]]);"""
    )
    assert result == [True, True]


def test_a_declared_group_puts_an_extension_entry_on_a_core_row_or_opens_its_own():
    result = run_recordings(
        """extensionExports = [
          { id: "txt", label: "TXT", extension: ".txt", group: "transcript" },
          { id: "docx", label: "Word", extension: ".docx", group: "transcript" },
          { id: "srt", label: "SRT", extension: ".srt", group: "subtitles" },
          { id: "named", label: "Named", extension: ".named.txt", group: "speakers" },
          { id: "sum-a", label: "Summary: A (Markdown)", extension: ".md", group: "summary-a",
            group_label: "Meeting summary", hint: "Made today, 17:04" },
          { id: "sum-a-docx", label: "A as Word", extension: ".docx", group: "summary-a" },
          { id: "plain", label: "Plain", extension: ".txt", group: "constructor" },
        ];
        out(exportRows({ id: 7 }).map((r) => [r.label, r.hint, r.chips.map((c) => c.format)]));"""
    )
    assert result == [
        ["Transcript", "Text only", ["TXT", "DOCX"]],
        ["Subtitles", "With timestamps", ["SRT"]],
        ["By speaker", "Uses your speaker names", ["TXT"]],
        ["Meeting summary", "Made today, 17:04", ["MD", "DOCX"]],
        ["Plain", "", ["TXT"]],
    ]


def test_a_declared_group_label_and_hint_replace_those_of_a_core_row():
    result = run_recordings(
        """extensionExports = [{ id: "txt", label: "TXT", extension: ".txt", group: "transcript",
          group_label: "Full text", hint: "Cleaned up" }];
        const r = exportRows({ id: 7 })[0]; out([r.label, r.hint, r.icon]);"""
    )
    assert result == ["Full text", "Cleaned up", "transcript"]


def test_a_rows_title_and_hint_do_not_depend_on_which_of_its_exports_can_run():
    result = run_recordings(
        """extensionExports = [
          { id: "sum-md", label: "A (Markdown)", extension: ".md", group: "summary-a",
            group_label: "Meeting summary", hint: "Made today" },
          { id: "sum-docx", label: "A as Word", extension: ".docx", group: "summary-a" },
        ];
        const head = (item) => exportRows(item).map((r) => [r.label, r.hint, r.icon, r.chips.map((c) => c.format)]);
        out([head({ id: 7 }), head({ id: 7, exports_unavailable: { "sum-md": "No summary yet" } }),
          head({ id: 7, exports_unavailable: { "sum-md": "x", "sum-docx": "x" } })]);"""
    )
    assert result == [
        [["Meeting summary", "Made today", "summary", ["MD", "DOCX"]]],
        [["Meeting summary", "Made today", "summary", ["DOCX"]]],
        [],
    ]


def test_an_extension_can_retitle_a_core_row_even_though_the_built_in_target_comes_first():
    result = run_recordings(
        """extensionExports = [
          { id: "txt", label: "TXT", extension: ".txt", group: "transcript" },
          { id: "docx", label: "Word", extension: ".docx", group: "transcript",
            group_label: "Full text", hint: "Cleaned up" },
        ];
        const row = (item) => exportRows(item)[0];
        const both = row({ id: 7 }), onlyTxt = row({ id: 7, exports_unavailable: { docx: "x" } });
        out([both.label, both.hint, onlyTxt.label, onlyTxt.hint, both.chips.map((c) => c.format)]);"""
    )
    assert result == ["Full text", "Cleaned up", "Full text", "Cleaned up", ["TXT", "DOCX"]]


def test_a_failure_goes_to_the_page_when_the_menu_was_redrawn_or_closed_meanwhile():
    result = run_recordings(
        CHIP
        + """(async () => {
          const menu = document.createElement("div"); menu.className = "rec-menu";
          rowsEl.remove(); menu.append(rowsEl); document.body.append(menu);
          globalThis.downloadExport = async () => "Export failed (500)";
          menu.hidden = false;
          await runExportChip(chip, detail);                    // open: shown in the menu
          const open = [errorEl.hidden, feedback.length];
          errorEl.hidden = true;
          menu.hidden = true;
          await runExportChip(chip, detail);                    // closed meanwhile: the page's feedback
          const closed = [errorEl.hidden, feedback.slice()];
          menu.hidden = false;
          globalThis.downloadExport = async () => { rowsEl.remove(); return "Export failed (500)"; };
          await runExportChip(chip, detail);                    // the head was redrawn: the menu is gone
          out([open, closed, feedback.length, feedback[1]]);
        })();"""
    )
    assert result == [[False, 0], [True, [["error", "Export failed (500)"]]], 2, ["error", "Export failed (500)"]]


def test_an_old_export_error_is_gone_when_the_menu_is_closed():
    result = run_recordings(
        """const wrap = document.createElement("div"); wrap.className = "rec-menu-wrap";
        const button = document.createElement("button"); button.className = "rec-menu-btn";
        const menu = document.createElement("div"); menu.className = "rec-menu rec-menu-export"; menu.hidden = true;
        const chip = document.createElement("button"); chip.className = "rec-chip rec-export";
        const error = document.createElement("p"); error.className = "rec-export-error"; error.hidden = true;
        menu.append(chip, error); wrap.append(button, menu);
        bindRecMenu(wrap);
        button.click();
        error.textContent = "No readable word timestamps"; error.hidden = false;
        wrap.dispatch("keydown", { key: "Escape" });
        button.click();
        out([error.hidden, menu.hidden]);"""
    )
    assert result == [True, False]


def test_a_tarball_chip_reads_tar_gz_and_other_suffixes_their_last_part():
    result = run_recordings(
        """const f = (extension) => exportFormat({ label: "X", extension });
        out([f(".tar.gz"), f(".TAR.xz"), f(".speakers.docx"), f(".md"), f("")]);"""
    )
    assert result == ["TAR.GZ", "TAR.XZ", "DOCX", "MD", "X"]


def test_a_group_named_like_a_lone_target_does_not_join_it_and_unknown_rows_get_a_neutral_icon():
    result = run_recordings(
        """extensionExports = [
          { id: "foo", label: "Foo (CSV)", extension: ".csv" },
          { id: "bar", label: "Bar (CSV)", extension: ".csv", group: "target:foo" },
          { id: "sum", label: "Sum (MD)", extension: ".md", group: "summary-x" },
        ];
        out(exportRows({ id: 7 }).map((r) => [r.label, r.icon, r.chips.map((c) => c.id)]));"""
    )
    assert result == [["Foo", "file", ["foo"]], ["Bar", "file", ["bar"]], ["Sum", "summary", ["sum"]]]
