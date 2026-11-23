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
    meetings = (STATIC / "dashboard-meetings.js").read_text(encoding="utf-8")
    clock = _section(meetings, "function formatClock", "function showMeetingStatus")  # the real clock format
    rows = f"const ROWS = {json.dumps(ROWS)};"
    parts = [prelude, FAKE_DOM, RECORDINGS_STUBS, pending, core, clock, recordings, rows, body]
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


# -- the Speakers panel ------------------------------------------------------------------------------

TIMED_LINES = r"""
const timedLines = [
  { who: "Speaker 1", colour: 0, text: "a", start: 0, end: 30 },
  { who: "Maria", colour: 1, text: "b", start: 30, end: 40 },
  { who: "Speaker 1", colour: 0, text: "c", start: 40, end: 70 },
  { who: "Speaker 3", colour: 2, text: "d", start: 70, end: 100 },
];
function showSpeakers(lines) {
  const d = makeDetail(ROWS[0]);
  d._lines = lines;
  recChosenTab = "speakers";
  renderRecordingTabs(d, ROWS[0]);
  return d;
}
const rowParts = (li) => ({
  speaker: li.dataset.speaker,
  avatar: li.querySelector(".rec-avatar").textContent,
  name: li.querySelector(".rec-speaker-name").textContent,
  bar: li.querySelector(".rec-speaker-fill") ? li.querySelector(".rec-speaker-fill").style.width : null,
  stats: li.querySelectorAll(".rec-stat").map((x) => x.textContent),
});
"""


def test_each_speaker_gets_one_row_with_avatar_share_time_and_turns():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers(timedLines);
        out([d.querySelectorAll(".rec-speaker").map(rowParts), d.querySelectorAll(".rec-speaker-list").length]);"""
    )
    rows, lists = result
    assert lists == 1
    assert rows == [
        {"speaker": "Speaker 1", "avatar": "1", "name": "Speaker 1", "bar": "60%", "stats": ["60% · 01:00", "2 turns"]},
        {"speaker": "Maria", "avatar": "M", "name": "Maria", "bar": "10%", "stats": ["10% · 00:10", "1 turn"]},
        {"speaker": "Speaker 3", "avatar": "3", "name": "Speaker 3", "bar": "30%", "stats": ["30% · 00:30", "1 turn"]},
    ]


def test_without_word_timing_a_row_shows_turns_only_and_no_bar():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers(transcriptLines(ROWS[0].text, [], "meeting"));
        out(d.querySelectorAll(".rec-speaker").map(rowParts));"""
    )
    assert result == [
        {"speaker": "Speaker 1", "avatar": "1", "name": "Speaker 1", "bar": None, "stats": ["1 turn"]},
        {"speaker": "Speaker 2", "avatar": "2", "name": "Speaker 2", "bar": None, "stats": ["1 turn"]},
    ]


def test_a_name_is_shown_as_text_and_its_initial_is_a_letter():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers([{ who: "<b>élan</b>", colour: 3, text: "x", start: null, end: null }]);
        const li = d.querySelector(".rec-speaker");
        out([rowParts(li), li.querySelector(".rec-speaker-name").children.length,
          li.querySelector(".rec-avatar").classList.contains("rec-fill-3")]);"""
    )
    parts, children, filled = result
    assert parts["name"] == "<b>élan</b>"
    assert parts["avatar"] == "<"  # first character, never markup that was parsed
    assert children == 0
    assert filled is True


def test_no_speaker_lines_show_the_empty_note_and_no_list():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers([]);
        const note = d.querySelector(".rec-speakers").innerHTML.includes("Detect speakers");
        out([d.querySelectorAll(".rec-speaker").length, note]);"""
    )
    assert result == [0, True]


def test_rows_offer_a_name_and_an_empty_actions_slot_for_extensions():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers(timedLines);
        const li = d.querySelector(".rec-speaker");
        const actions = li.querySelector('[data-slot="actions"]');
        out([li.querySelector('[data-slot="name"]').textContent, actions.children.length, actions.textContent,
          li.dataset.colour]);"""
    )
    assert result == ["Speaker 1", 0, "", "0"]


def test_a_speaker_rows_hook_gets_every_row_in_the_page_and_a_failing_one_is_skipped():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const calls = [];
        const d = showSpeakers(timedLines);
        const hook = ({ recording, rows }) => {
          const figures = rows.map((r) => [r.speaker, r.turns, r.seconds, r.share]);
          calls.push([recording.id, figures, rows[0].row.isConnected]);
          for (const r of rows) {
            const b = document.createElement("button");
            b.textContent = "...";
            r.actions.append(b);
          }
        };
        const added = addSpeakerRowsHook("pro", hook);  // the open Speakers panel is drawn again at once
        console.error = () => {};
        const bad = addSpeakerRowsHook("bad", () => { throw new Error("boom"); });
        const invalid = [addSpeakerRowsHook("Bad Id", hook), addSpeakerRowsHook("x", null)];
        renderRecordingTabs(d, ROWS[0]);
        out([added, bad, invalid, calls.length, calls[0], d.querySelectorAll(".rec-speaker-actions button").length,
          d.querySelectorAll(".rec-speaker").length]);"""
    )
    added, bad, invalid, count, first, buttons, rows = result
    assert (added, bad, invalid) == (True, True, [False, False])
    assert count == 3  # on registering, on the failing hook registering, then the explicit draw
    expected = [["Speaker 1", 2, 60, 60], ["Maria", 1, 10, 10], ["Speaker 3", 1, 30, 30]]
    assert first == [7, expected, True]  # the rows are in the page when the hook runs
    assert (buttons, rows) == (3, 3)  # the panel was rebuilt each time, so the buttons are not stacked up


def test_registering_an_id_again_replaces_its_hook_and_unregistering_removes_it():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers(timedLines);
        const mark = (text) => ({ rows }) => { for (const r of rows) r.actions.append(text); };
        addSpeakerRowsHook("pro", mark("a"));
        addSpeakerRowsHook("pro", mark("b"));  // a script that ran twice
        const texts = () => d.querySelectorAll(".rec-speaker-actions").map((x) => x.textContent);
        const twice = texts();
        const removed = [removeSpeakerRowsHook("pro"), removeSpeakerRowsHook("pro")];
        out([twice, removed, texts(), speakerRowHooks.length]);"""
    )
    assert result == [["b", "b", "b"], [True, False], ["", "", ""], 0]


def test_the_panel_is_not_rebuilt_while_a_row_is_in_use_and_catches_up_afterwards():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers(timedLines);
        const field = document.createElement("input");
        d.querySelector(".rec-speaker-actions").append(field);
        field.focus();
        const list = d.querySelector(".rec-speaker-list");
        renderRecordingTabs(d, ROWS[0]);  // e.g. the turns finished loading
        const kept = d.querySelector(".rec-speaker-list") === list
          && d.querySelector(".rec-speaker-actions input") === field;
        const stale = d._speakersStale;
        document.body.focus();  // the focus leaves the list
        list.dispatch("focusout", { relatedTarget: null });
        setTimeout(() => {
          out([kept, stale, d.querySelector(".rec-speaker-list") !== list, d._speakersStale]);
        }, 20);"""
    )
    assert result == [True, True, True, False]


def test_a_row_an_extension_marked_busy_is_kept_until_it_is_released():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """let release;
        addSpeakerRowsHook("pro", ({ setBusy }) => { release = setBusy; });
        const d = showSpeakers(timedLines);
        release(true);
        const list = d.querySelector(".rec-speaker-list");
        renderRecordingTabs(d, ROWS[0]);
        renderRecordingTabs(d, ROWS[0]);
        const held = d.querySelector(".rec-speaker-list") === list;
        release(false);
        setTimeout(() => out([held, d.querySelector(".rec-speaker-list") !== list]), 20);"""
    )
    assert result == [True, True]


def test_switching_tabs_is_never_held_back_by_a_row_in_use():
    result = run_recordings(
        DETAIL
        + TIMED_LINES
        + """const d = showSpeakers(timedLines);
        d.querySelector(".rec-speaker-list").dataset.busy = "1";
        chooseRecordingTab("transcript");
        out([chosen(d), d.querySelectorAll(".rec-speaker-list").length]);"""
    )
    assert result == ["transcript", 0]


def test_shares_always_add_up_to_100():
    result = run_recordings(
        """const lines = (secs) => secs.map((s, i) => ({ who: "S" + i, colour: i, text: "", start: 0, end: s }));
        const shares = (secs) => speakerStats(lines(secs)).map((s) => s.share);
        out([shares([10, 10, 10]), shares([1, 1, 1, 997]), shares([60, 10, 30]), shares([0, 0]), shares([5, 0])]);"""
    )
    thirds, tiny, plain, none, lone = result
    assert thirds == [34, 33, 33]
    assert tiny == [0, 0, 0, 100]
    assert plain == [60, 10, 30]
    assert none == [None, None]  # no timed talk at all: no shares
    assert lone == [100, 0]
    assert sum(thirds) == sum(tiny) == 100


def test_the_extension_object_exposes_the_speaker_rows_hook():
    core = (STATIC / "dashboard-core.js").read_text(encoding="utf-8")
    assert "registerSpeakerRows(id, fn) {\n    return addSpeakerRowsHook(id, fn);" in core
    assert "unregisterSpeakerRows(id) {\n    return removeSpeakerRowsHook(id);" in core
