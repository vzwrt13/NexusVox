/* NexusVox Dashboard — Recordings tab: the list of meetings and uploads, the detail pane, extension tabs */

// ── Recordings ───────────────────────────────────────────────────────
// One list holds meetings and uploaded files, newest first. The chosen one fills the detail pane: title and
// meta, Export and More menus, the audio player and a tab row (Summary, Transcript, Speakers and the tabs of
// extensions). The list redraws freely; the detail pane keeps its player, the chosen tab and every extension
// tab's content for as long as the same recording stays chosen.
const recList = document.getElementById("rec-list");
const recDetailPane = document.getElementById("rec-detail");
const recSearch = document.getElementById("rec-search");
const recOptions = document.getElementById("rec-options");
const recOptionsToggle = document.getElementById("rec-options-toggle");

const PRO_URL = "https://nexusvox.app/pro";
const REC_LIST_LIMIT = 100;
const CORE_RECORDING_TABS = ["summary", "transcript", "speakers"];

let recItems = []; // the rows of the last load, as GET /api/file-transcriptions lists them
let recSelectedId = null; // id (string) of the recording in the detail pane
let recChosenTab = "transcript"; // the tab the user chose; kept across redraws and other recordings
let recMark = null; // {id, start, end, text}: the passage an extension asked to mark in the transcript
const recShowOriginal = new Set(); // ids of cleaned recordings switched to the transcript as transcribed
const recTurns = new Map(); // "id:text" -> Promise of the speaker turns [{speaker, start, end}]
const recordingTabs = []; // extension tabs: {id, title, render, available}
// "tab:recording" -> the container an extension tab drew into, least recently shown first. At most
// REC_TAB_KEEP are kept (the shown recording's never drop out); older ones are drawn again when shown.
const recTabContent = new Map();
const REC_TAB_KEEP = 24;
const recordingScriptsLoaded = new Set(); // panel ids whose recording_script was added to the page
let recPending = false; // the last load had a row with a pending state
let recLoadGeneration = 0; // a load that is no longer the newest drops its answer

// ── Options popover ──────────────────────────────────────────────────
function setRecOptionsOpen(open) {
  recOptions.hidden = !open;
  recOptionsToggle.setAttribute("aria-expanded", String(open));
}
recOptionsToggle.addEventListener("click", () => setRecOptionsOpen(recOptions.hidden));
recOptions.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  setRecOptionsOpen(false);
  recOptionsToggle.focus();
});

// One outside click closes whatever popover or menu is open in the tab.
document.addEventListener("click", (e) => {
  if (!recOptions.hidden && !recOptions.contains(e.target) && !recOptionsToggle.contains(e.target)) {
    setRecOptionsOpen(false);
  }
  document.querySelectorAll(".rec-menu-wrap.open").forEach((wrap) => {
    if (!wrap.contains(e.target)) closeRecMenu(wrap);
  });
});

// ── Resizable list pane ──────────────────────────────────────────────
// The border between the panes is a handle (#rec-resizer): drag it, or focus it and use the arrow keys (Shift for
// bigger steps, Home and End for the limits), to set the list's width between REC_LIST_MIN and half the tab; a
// double click goes back to the default. The width is kept in the browser's localStorage, so it survives a reload
// and a restart of the app. recordings.css clamps it again, so a width chosen in a wide window fits a narrow one.
const recLayout = document.getElementById("rec-layout");
const recListPane = document.getElementById("rec-list-pane");
const recResizer = document.getElementById("rec-resizer");
const REC_LIST_MIN = 240;
const REC_LIST_STEP = 16;
const REC_LIST_WIDTH_KEY = "recordings-list-width";
let recDrag = null; // {pointerId, startX, startWidth, max, width} while the handle is dragged

function recListMax() {
  return Math.max(REC_LIST_MIN, Math.floor(recLayout.getBoundingClientRect().width / 2));
}

function clampRecListWidth(width, max) {
  return Math.round(Math.min(Math.max(width, REC_LIST_MIN), Math.max(REC_LIST_MIN, max)));
}

function shownRecListWidth() {
  return Math.round(recListPane.getBoundingClientRect().width);
}

function storedRecListWidth() {
  try {
    const width = Number(localStorage.getItem(REC_LIST_WIDTH_KEY));
    return Number.isFinite(width) && width > 0 ? width : null;
  } catch {
    return null; // storage switched off: the default width
  }
}

// `width` null is the default. `save` stores it; while dragging only the end of the drag is stored.
function setRecListWidth(width, save) {
  if (width === null) recLayout.style.removeProperty("--rec-list-width");
  else recLayout.style.setProperty("--rec-list-width", `${width}px`);
  if (save) {
    try {
      if (width === null) localStorage.removeItem(REC_LIST_WIDTH_KEY);
      else localStorage.setItem(REC_LIST_WIDTH_KEY, String(width));
    } catch {
      // storage switched off: the width holds until the page is reloaded
    }
  }
  syncRecResizer();
}

function syncRecResizer() {
  recResizer.setAttribute("aria-valuemax", String(recListMax()));
  recResizer.setAttribute("aria-valuenow", String(shownRecListWidth()));
}

recResizer.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  const startWidth = shownRecListWidth();
  recDrag = { pointerId: e.pointerId, startX: e.clientX, startWidth, max: recListMax(), width: startWidth };
  recResizer.setPointerCapture?.(e.pointerId);
  recLayout.classList.add("resizing");
});
recResizer.addEventListener("pointermove", (e) => {
  if (!recDrag || e.pointerId !== recDrag.pointerId) return;
  recDrag.width = clampRecListWidth(recDrag.startWidth + e.clientX - recDrag.startX, recDrag.max);
  setRecListWidth(recDrag.width, false);
});
function endRecDrag(e) {
  if (!recDrag || e.pointerId !== recDrag.pointerId) return;
  const { width, startWidth } = recDrag;
  recDrag = null;
  recLayout.classList.remove("resizing");
  if (width !== startWidth) setRecListWidth(width, true);
}
recResizer.addEventListener("pointerup", endRecDrag);
recResizer.addEventListener("pointercancel", endRecDrag);
recResizer.addEventListener("dblclick", () => setRecListWidth(null, true));
recResizer.addEventListener("focus", syncRecResizer);
recResizer.addEventListener("keydown", (e) => {
  const step = e.shiftKey ? REC_LIST_STEP * 4 : REC_LIST_STEP;
  const max = recListMax();
  const now = shownRecListWidth();
  const target = { ArrowLeft: now - step, ArrowRight: now + step, Home: REC_LIST_MIN, End: max }[e.key];
  if (target === undefined) return;
  e.preventDefault();
  setRecListWidth(clampRecListWidth(target, max), true);
});

const savedRecListWidth = storedRecListWidth();
if (savedRecListWidth !== null) recLayout.style.setProperty("--rec-list-width", `${savedRecListWidth}px`);

// ── Opening the tab, loading the list ────────────────────────────────
function recordingsShown() {
  return document.getElementById("tab-recordings").classList.contains("active");
}

function openRecordingsTab() {
  openMeetingRecording();
  loadFileTranscriptions();
  loadLlmStatus().catch(() => {}); // the cleanup hint must reflect Ollama as it is now
  refreshDiarizeToggle();
  resumeUploadJob();
  loadRecordingTabScripts();
}

// An extension registers its detail-pane tabs from a light script of their own (`recording_script` of its
// panel), added once when the Recordings tab first opens. The panel's main script is not loaded for this: it
// runs only when its own sidebar tab is opened, so a panel that polls or starts timers stays quiet until then.
function loadRecordingTabScripts() {
  const loading = [];
  for (const [id, panel] of Object.entries(extensionPanels)) {
    if (!panel.recordingScript || recordingScriptsLoaded.has(id)) continue;
    recordingScriptsLoaded.add(id);
    const script = document.createElement("script");
    script.src = panel.recordingScript;
    loading.push(new Promise((resolve) => {
      script.onload = resolve;
      script.onerror = () => {
        console.error("Recording tab script failed to load:", panel.recordingScript);
        resolve();
      };
    }));
    document.head.appendChild(script);
  }
  if (loading.length) Promise.all(loading).then(() => renderSelectedRecording(false));
}

const recPendingPoller = createPendingPoller(() => {
  if (document.hidden || !recordingsShown()) return; // the visibilitychange handler and the tab pick it up again
  loadFileTranscriptions(true);
}, PENDING_POLL_MS);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && recPending && recordingsShown() && !recPendingPoller.active) loadFileTranscriptions(true);
});

// Never rejects: a failed refresh keeps the polling going (or leaves it off) as it was. A `poll` skips the
// recordings that still wait for their transcript (that list does not change on its own).
async function loadFileTranscriptions(poll = false) {
  const generation = ++recLoadGeneration;
  try {
    const [rows] = await Promise.all([fetchRecordingRows(), poll ? null : loadPendingMeetings()]);
    if (generation !== recLoadGeneration) return;
    applyRecordingRows(rows, poll);
  } catch (err) {
    console.error("Failed to load the recordings:", err);
  }
  if (generation === recLoadGeneration) recPendingPoller.update(recPending && recordingsShown());
}

function fetchRecordingRows() {
  return fetch(`/api/file-transcriptions?limit=${REC_LIST_LIMIT}`).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
}

function applyRecordingRows(rows, poll) {
  recItems = rows;
  recPending = rowsHavePending(rows);
  pruneRecordingCaches(rows);
  renderRecordingList();
  renderSelectedRecording(poll);
}

// What is kept per recording (extension tab content, speaker turns, the original/cleaned switch) goes once the
// recording is no longer listed (deleted, or pushed out of the list by newer ones).
function pruneRecordingCaches(rows) {
  const listed = new Set(rows.map((r) => String(r.id)));
  for (const key of [...recTabContent.keys()]) if (!listed.has(key.split(":")[1])) recTabContent.delete(key);
  for (const key of [...recTurns.keys()]) if (!listed.has(key.split(":")[0])) recTurns.delete(key);
  for (const id of [...recShowOriginal]) if (!listed.has(id)) recShowOriginal.delete(id);
}

// ── The list ─────────────────────────────────────────────────────────
function recordingTitle(item) {
  return item.title || item.original_filename;
}

function formatRecordingLength(ms) {
  const seconds = Math.round((ms || 0) / 1000);
  if (seconds < 60) return seconds + " s";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return minutes + " min";
  return Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}

function recordingDate(item, withTime) {
  if (!item.created_at) return "";
  const options = withTime
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { month: "short", day: "numeric" };
  return new Date(item.created_at).toLocaleString(undefined, options);
}

function recordingListMeta(item) {
  const parts = [recordingDate(item, false), formatRecordingLength(item.duration_ms)];
  if (item.kind !== "meeting") parts.push("Upload");
  return parts.filter(Boolean).join(" · ");
}

function matchesRecordingSearch(item, query) {
  if (!query) return true;
  return [recordingTitle(item), item.original_filename, item.text, item.cleaned_text].some(
    (t) => (t || "").toLowerCase().includes(query));
}

function shownRecordings() {
  const query = recSearch.value.trim().toLowerCase();
  return recItems.filter((item) => matchesRecordingSearch(item, query));
}

function recordingRowHtml(item) {
  return `<button type="button" class="rec-row" data-id="${item.id}" data-kind="${item.kind === "meeting" ? "meeting" : "upload"}">
      <span class="rec-row-icon" aria-hidden="true"></span>
      <span class="rec-row-text">
        <span class="rec-row-title">${escapeHtml(recordingTitle(item))}</span>
        <span class="rec-row-meta">${escapeHtml(recordingListMeta(item))}<span class="visually-hidden">${item.kind === "meeting" ? ", meeting" : ""}</span></span>
      </span>
    </button>`;
}

// Keyed by recording: a row whose content did not change keeps its element, and rows move only when the order
// changed, so a refresh (the pending poll runs every few seconds) never takes the keyboard focus or a screen
// reader's place away from the row the user is on.
function renderRecordingList() {
  const shown = shownRecordings();
  const active = document.activeElement;
  const focusedId = active && recList.contains(active) && active.dataset ? active.dataset.id : undefined;
  const message = !recItems.length ? "No recordings yet." : !shown.length ? "Nothing matches your search." : "";
  if (message) {
    recList.innerHTML = `<li class="empty-state">${message}</li>`;
    return;
  }
  const existing = new Map([...recList.children].filter((li) => li.dataset.id).map((li) => [li.dataset.id, li]));
  const rows = shown.map((item) => {
    const id = String(item.id);
    let li = existing.get(id);
    existing.delete(id);
    if (!li) {
      li = document.createElement("li");
      li.dataset.id = id;
    }
    const html = recordingRowHtml(item);
    if (li._html !== html) {
      li.innerHTML = html;
      li._html = html;
    }
    return li;
  });
  [...recList.children].forEach((child) => {
    if (!child.dataset.id || existing.has(child.dataset.id)) child.remove();
  });
  rows.forEach((li, i) => {
    if (recList.children[i] !== li) recList.insertBefore(li, recList.children[i] || null);
  });
  markSelectedRow();
  if (focusedId !== undefined && !recList.contains(document.activeElement)) {
    const again = recList.querySelector(`.rec-row[data-id="${focusedId}"]`);
    if (again) again.focus({ preventScroll: true });
  }
}

function markSelectedRow() {
  recList.querySelectorAll(".rec-row").forEach((row) => {
    row.setAttribute("aria-current", String(row.dataset.id === recSelectedId));
  });
}

recList.addEventListener("click", (e) => {
  const row = e.target.closest(".rec-row");
  if (row) selectRecording(row.dataset.id);
});

// Up and Down move through the list, like a list box.
recList.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const rows = [...recList.querySelectorAll(".rec-row")];
  const at = rows.indexOf(document.activeElement);
  if (at < 0) return;
  e.preventDefault();
  const next = rows[Math.min(rows.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)))];
  next.focus();
  selectRecording(next.dataset.id);
});

recSearch.addEventListener("input", () => {
  renderRecordingList();
  renderSelectedRecording(false);
});

function selectRecording(id) {
  recSelectedId = String(id);
  // A recording chosen in the list is what "#recordings/<id>" names now, so a reload does not jump back; the entry
  // is rewritten, not followed by a new one, so Back still has one step to take.
  if (window.location.hash.startsWith("#recordings/")) replaceHash("#recordings/" + encodeURIComponent(id));
  markSelectedRow();
  renderSelectedRecording(false);
}

// ── The detail pane ──────────────────────────────────────────────────
// What the head and the transcript show; a poll that brings the same signature leaves the pane alone.
function recordingSignature(item) {
  const running = extensionUploadActions.filter((a) => runningUploadActions.has(`${a.id}:${item.id}`)).map((a) => a.id);
  return JSON.stringify([item.id, item.exports_unavailable, item.actions_unavailable, running, item.has_audio,
    item.original_filename, item.title, item.language, item.model, (item.text || "").length,
    (item.cleaned_text || "").length]);
}

function currentDetail() {
  return recDetailPane.querySelector(":scope > .rec-detail");
}

// The recording the detail pane shows: the chosen one as long as it is listed, also while the search hides
// its row (typing must not stop its audio or drop a confirmation), else the first one shown. `poll` keeps a
// pane whose recording did not change untouched.
function renderSelectedRecording(poll) {
  const shown = shownRecordings();
  let item = recItems.find((i) => String(i.id) === recSelectedId);
  if (!item) item = shown[0];
  const detail = currentDetail();
  if (!item) {
    if (detail) detail.remove();
    const empty = document.getElementById("rec-detail-empty");
    empty.hidden = false;
    empty.textContent = recItems.length
      ? "Nothing matches your search."
      : "No recordings yet. Start a recording or upload an audio file.";
    return;
  }
  document.getElementById("rec-detail-empty").hidden = true;
  if (String(item.id) !== recSelectedId) selectRecordingRow(item.id);
  const signature = recordingSignature(item);
  if (detail && detail.dataset.id === String(item.id)) {
    const textChanged = detail._textKey !== recordingTextKey(item);
    detail._item = item;
    if (poll && detail._signature === signature) return;
    detail._signature = signature;
    if (!detail._editing) fillRecordingHead(detail, item);
    if (textChanged) drawRecordingBody(detail, item);
    else renderRecordingTabs(detail, item);
    return;
  }
  if (detail) detail.remove();
  const fresh = buildRecordingDetail(item);
  fresh._signature = signature;
  recDetailPane.append(fresh);
  recDetailPane.scrollTop = 0;
}

function selectRecordingRow(id) {
  recSelectedId = String(id);
  markSelectedRow();
}

function recordingTextKey(item) {
  return JSON.stringify([(item.text || "").length, (item.cleaned_text || "").length, (item.text || "").slice(0, 200),
    recShowOriginal.has(String(item.id))]);
}

function buildRecordingDetail(item) {
  const detail = document.createElement("article");
  detail.className = "rec-detail";
  detail.dataset.id = String(item.id);
  detail._item = item;
  detail.innerHTML = `
    <div class="rec-head"></div>
    <div class="rec-states"></div>
    <div class="rec-delete-row" hidden></div>
    <div class="upload-error rec-detail-error" style="display: none;"></div>
    <div class="upload-note rec-detail-note" style="display: none;"></div>
    ${item.has_audio ? recordingPlayerHtml(item) : ""}
    <div class="rec-tabs" role="tablist" aria-label="Recording content"></div>
    <div class="rec-panels"></div>`;
  if (item.has_audio) bindRecordingPlayer(detail);
  fillRecordingHead(detail, item);
  drawRecordingBody(detail, item);
  return detail;
}

// ── Head: title, meta, Export and More menus, row states ─────────────
function recordingDetailMeta(item, speakerCount) {
  const parts = [recordingDate(item, true), formatRecordingLength(item.duration_ms)];
  if (speakerCount > 1) parts.push(speakerCount + " speakers");
  if (item.language) parts.push(item.language.toUpperCase());
  parts.push(item.kind === "meeting" ? "Meeting" : "Upload");
  if (item.model) parts.push(item.model);
  if (item.cleaned_text) parts.push("Cleaned up" + (item.cleanup_level ? ` (${item.cleanup_level})` : ""));
  return parts.filter(Boolean).join(" · ");
}

function fillRecordingHead(detail, item) {
  const head = detail.querySelector(".rec-head");
  head.innerHTML = `
    <div class="rec-head-text">
      <div class="rec-title-row">
        <h2 class="rec-title">${escapeHtml(recordingTitle(item))}</h2>
        <button type="button" class="rec-icon-btn rec-rename" title="Rename" aria-label="Rename">✎</button>
      </div>
      <p class="rec-meta">${escapeHtml(recordingDetailMeta(item, detail._speakerCount || 0))}</p>
    </div>
    <div class="rec-head-actions">
      <div class="rec-menu-wrap">
        <button type="button" class="rec-menu-btn" aria-haspopup="true" aria-expanded="false">Export <span aria-hidden="true">▾</span></button>
        <div class="rec-menu" hidden>${exportMenuHtml(item)}</div>
      </div>
      <div class="rec-menu-wrap">
        <button type="button" class="rec-menu-btn rec-more-btn" aria-haspopup="true" aria-expanded="false" aria-label="More actions">⋯</button>
        <div class="rec-menu" hidden>${moreMenuHtml(item)}</div>
      </div>
    </div>`;
  head.querySelectorAll(".rec-menu-wrap").forEach(bindRecMenu);
  head.querySelector(".rec-rename").addEventListener("click", () => editFileTitle(detail));
  head.querySelectorAll(".rec-export").forEach((entry) => {
    entry.addEventListener("click", () => downloadExport(entry.dataset.href, detail._item));
  });
  head.querySelectorAll(".upload-action").forEach((button) => {
    button.addEventListener("click", () => runUploadAction(button, detail._item));
  });
  head.querySelector(".rec-rename-item").addEventListener("click", () => editFileTitle(detail));
  head.querySelector(".rec-delete-item").addEventListener("click", () => askDeleteRecording(detail));

  const states = detail.querySelector(".rec-states");
  states.innerHTML = renderRowStates(item);
  states.querySelectorAll(".upload-retry").forEach((button) => {
    button.addEventListener("click", () => runUploadAction(button, detail._item));
  });
}

// An entry that cannot run says why in the menu itself: nothing is greyed out without a reason.
function menuReason(reason) {
  const state = availabilityState(reason);
  return `<span class="rec-menu-reason">${escapeHtml(state.text)}</span>`;
}

function exportMenuHtml(item) {
  if (!extensionExports.length) return `<p class="rec-menu-empty">No export formats are available.</p>`;
  return extensionExports.map((t) => {
    const reason = (item.exports_unavailable || {})[t.id];
    if (reason !== undefined) {
      return `<button type="button" class="rec-menu-item" disabled>${escapeHtml(t.label)}${menuReason(reason)}</button>`;
    }
    const href = `/api/file-transcriptions/${item.id}/export/${encodeURIComponent(t.id)}`;
    return `<button type="button" class="rec-menu-item rec-export" data-href="${escapeHtml(href)}">${escapeHtml(t.label)}</button>`;
  }).join("");
}

function moreMenuHtml(item) {
  const actions = extensionUploadActions.map((a) => {
    const reason = (item.actions_unavailable || {})[a.id];
    const running = runningUploadActions.has(`${a.id}:${item.id}`);
    const off = reason !== undefined || running;
    const why = reason !== undefined ? menuReason(reason) : running ? `<span class="rec-menu-reason">Running…</span>` : "";
    return `<button type="button" class="rec-menu-item upload-action" data-action="${escapeHtml(a.id)}"${off ? " disabled" : ""}>${escapeHtml(a.label)}${why}</button>`;
  }).join("");
  return `${actions}${actions ? `<hr class="rec-menu-sep">` : ""}
    <button type="button" class="rec-menu-item rec-rename-item">Rename</button>
    <button type="button" class="rec-menu-item rec-delete-item rec-menu-danger">Delete…</button>`;
}

function bindRecMenu(wrap) {
  const button = wrap.querySelector(".rec-menu-btn");
  const menu = wrap.querySelector(".rec-menu");
  button.addEventListener("click", () => {
    const open = menu.hidden;
    document.querySelectorAll(".rec-menu-wrap.open").forEach(closeRecMenu);
    if (!open) return;
    wrap.classList.add("open");
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    const first = menu.querySelector(".rec-menu-item:not([disabled])");
    if (first) first.focus();
  });
  menu.addEventListener("click", (e) => {
    if (e.target.closest(".rec-menu-item:not([disabled])")) closeRecMenu(wrap);
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) {
      closeRecMenu(wrap);
      button.focus();
    }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !menu.hidden) {
      const items = [...menu.querySelectorAll(".rec-menu-item:not([disabled])")];
      if (!items.length) return;
      e.preventDefault();
      const at = items.indexOf(document.activeElement);
      items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
    }
  });
}

function closeRecMenu(wrap) {
  wrap.classList.remove("open");
  wrap.querySelector(".rec-menu").hidden = true;
  wrap.querySelector(".rec-menu-btn").setAttribute("aria-expanded", "false");
}

// The pending labels (spinner) and failure reasons (with Retry) of a row, one line each. Retry is offered only
// when the action it names is on this page and not greyed out for this row (its own failure, naming itself, is
// the one exception: that is how it is run again); while it runs, the button is disabled.
function renderRowStates(item) {
  const lines = [];
  const states = [
    ...extensionExports.map((t) => ({ label: t.label, reason: (item.exports_unavailable || {})[t.id] })),
    ...extensionUploadActions.map((a) => ({ label: a.label, reason: (item.actions_unavailable || {})[a.id], actionId: a.id })),
  ];
  for (const { label, reason, actionId } of states) {
    if (reason === undefined) continue;
    const state = availabilityState(reason);
    if (state.kind === "pending") {
      lines.push(`<div class="upload-state" role="status"><span class="model-status-spinner"></span><span>${escapeHtml(label)}: ${escapeHtml(state.text)}</span></div>`);
    } else if (state.kind === "error") {
      const own = (item.actions_unavailable || {})[state.retry];
      const offered = extensionUploadActions.some((a) => a.id === state.retry)
        && (own === undefined || (actionId === state.retry && availabilityState(own).kind === "error"));
      const busy = runningUploadActions.has(`${state.retry}:${item.id}`);
      const retry = offered
        ? ` <button type="button" class="upload-retry" data-action="${escapeHtml(state.retry)}"${busy ? " disabled" : ""}>Retry</button>` : "";
      lines.push(`<div class="upload-state down"><span>✕ ${escapeHtml(label)}: ${escapeHtml(state.text)}</span>${retry}</div>`);
    }
  }
  return lines.join("");
}

// Swaps the title for a text field: Enter saves, Esc or leaving the field cancels. An empty title brings the file
// or folder name back. The server keeps `original_filename`; only the title changes.
function editFileTitle(detail) {
  if (detail._editing) return;
  const row = detail.querySelector(".rec-title-row");
  const item = detail._item;
  const current = recordingTitle(item);
  const input = document.createElement("input");
  input.type = "text";
  input.className = "upload-rename-input";
  input.maxLength = 255;
  input.value = current;
  input.setAttribute("aria-label", "Name");
  detail._editing = true;
  row.replaceChildren(input);
  input.focus();
  input.select();
  const restore = (error) => {
    detail._editing = false;
    fillRecordingHead(detail, detail._item);
    if (error) {
      const note = document.createElement("p");
      setStatus(note, "down", "✕ " + error);
      detail.querySelector(".rec-head-text").append(note);
    }
  };
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const typed = input.value.trim();
    if (!save || typed === current) return restore();
    input.disabled = true;
    try {
      // Typing the folder/file name back is the same as clearing the title.
      const resp = await fetch(`/api/file-transcriptions/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: typed === item.original_filename ? "" : typed }),
      });
      if (!resp.ok) throw new Error((await resp.json()).error || "Rename failed");
    } catch (err) {
      return restore("Rename failed: " + err.message);
    }
    detail._editing = false;
    detail._signature = null; // the head is drawn again with the new title
    loadFileTranscriptions();
  };
  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return; // Enter confirms an IME composition, it is not a save
    if (event.key === "Enter") finish(true);
    else if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(false));
}

// The confirmation sits in the detail pane, never in a browser dialog.
function askDeleteRecording(detail) {
  const row = detail.querySelector(".rec-delete-row");
  const what = detail._item.kind === "meeting" ? "meeting and its audio files" : "recording and its audio";
  row.hidden = false;
  row.innerHTML = `
    <span class="upload-delete-ask">Delete this ${what}? This cannot be undone.</span>
    <button type="button" class="period-btn upload-delete-btn rec-delete-confirm">Delete</button>
    <button type="button" class="period-btn rec-delete-cancel">Cancel</button>
    <span class="upload-delete-error" hidden></span>`;
  row.querySelector(".rec-delete-cancel").addEventListener("click", () => {
    row.hidden = true;
    row.replaceChildren();
  });
  const confirm = row.querySelector(".rec-delete-confirm");
  confirm.focus();
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    const id = detail.dataset.id;
    const resp = await fetch(`/api/file-transcriptions/${id}`, { method: "DELETE" });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok || !result.ok) {
      const err = row.querySelector(".upload-delete-error");
      setStatus(err, "down", "✕ " + (result.error || "Delete failed"));
      err.hidden = false;
      confirm.disabled = false;
      return;
    }
    // The next recording in the list takes its place.
    const shown = shownRecordings();
    const at = shown.findIndex((i) => String(i.id) === id);
    const next = shown[at + 1] || shown[at - 1];
    recSelectedId = next ? String(next.id) : null;
    recTurns.delete(id);
    loadFileTranscriptions();
  });
}

// ── Audio player ─────────────────────────────────────────────────────
// A dark player of its own instead of the browser's white one: play/pause, a seek bar and the clock.
function recordingPlayerHtml(item) {
  const total = Math.round((item.duration_ms || 0) / 1000);
  return `
    <div class="rec-player">
      <audio preload="none" src="/api/file-transcriptions/${item.id}/audio"></audio>
      <button type="button" class="rec-play" aria-label="Play"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16"><path class="rec-play-icon" d="M4 2.5v11l9-5.5Z" fill="currentColor"/></svg></button>
      <input type="range" class="rec-seek" min="0" max="${total || 1}" step="0.1" value="0" aria-label="Position">
      <span class="rec-clock">${formatClock(0)} / ${formatClock(total)}</span>
    </div>`;
}

function bindRecordingPlayer(detail) {
  const audio = detail.querySelector(".rec-player audio");
  const play = detail.querySelector(".rec-play");
  const seek = detail.querySelector(".rec-seek");
  const clock = detail.querySelector(".rec-clock");
  const icon = detail.querySelector(".rec-play-icon");
  let total = Number(seek.max);
  const show = () => {
    seek.value = audio.currentTime;
    seek.style.setProperty("--progress", total ? (audio.currentTime / total) * 100 + "%" : "0%");
    clock.textContent = `${formatClock(Math.floor(audio.currentTime))} / ${formatClock(Math.round(total))}`;
  };
  play.addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  audio.addEventListener("play", () => {
    play.setAttribute("aria-label", "Pause");
    icon.setAttribute("d", "M4 2.5h3v11H4ZM9 2.5h3v11H9Z");
  });
  audio.addEventListener("pause", () => {
    play.setAttribute("aria-label", "Play");
    icon.setAttribute("d", "M4 2.5v11l9-5.5Z");
  });
  audio.addEventListener("loadedmetadata", () => {
    if (Number.isFinite(audio.duration)) {
      total = audio.duration;
      seek.max = total;
    }
    show();
  });
  audio.addEventListener("timeupdate", show);
  seek.addEventListener("input", () => {
    audio.currentTime = Number(seek.value);
    show();
  });
  detail._seek = (seconds) => {
    audio.currentTime = seconds;
    show();
    audio.play();
  };
}

// ── Body: the tab row and its panels ─────────────────────────────────
// The speaker turns come from the word timing; until they are in (or when there are none) the transcript shows
// without times.
// Kept per recording and text: an action that rewrites a transcript (and its word timing) brings new text,
// so its turns are fetched again. A failed fetch is not kept, so the next draw asks again.
function loadRecordingTurns(item) {
  const key = `${item.id}:${(item.text || "").length}:${(item.text || "").slice(0, 120)}`;
  if (!recTurns.has(key)) {
    recTurns.set(key, fetch(`/api/file-transcriptions/${item.id}/turns`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((body) => body.turns || [])
      .catch(() => {
        recTurns.delete(key);
        return [];
      }));
  }
  return recTurns.get(key);
}

function drawRecordingBody(detail, item) {
  detail._textKey = recordingTextKey(item);
  detail._lines = transcriptLines(shownRecordingText(item), [], item.kind);
  renderRecordingTabs(detail, item);
  loadRecordingTurns(item).then((turns) => {
    if (!detail.isConnected || detail._textKey !== recordingTextKey(detail._item)) return;
    detail._turns = turns;
    detail._lines = transcriptLines(shownRecordingText(detail._item), turns, detail._item.kind);
    detail._speakerCount = new Set(detail._lines.map((l) => l.who).filter(Boolean)).size;
    if (!detail._editing) {
      detail.querySelector(".rec-meta").textContent = recordingDetailMeta(detail._item, detail._speakerCount);
    }
    renderRecordingTabs(detail, detail._item);
  });
}

function shownRecordingText(item) {
  if (item.cleaned_text && !recShowOriginal.has(String(item.id))) return item.cleaned_text;
  return item.text || "";
}

// The tabs of one recording: Summary first (an extension's tab with the id "summary", else the Pro note),
// then Transcript and Speakers, then the other extension tabs in the order they were registered.
function recordingTabList(item) {
  const own = recordingTabs.filter((tab) => recordingTabAvailable(tab, item));
  const summary = own.find((tab) => tab.id === "summary");
  return [
    summary ? { id: "summary", title: summary.title, ext: summary } : { id: "summary", title: "Summary", pro: true },
    { id: "transcript", title: "Transcript" },
    { id: "speakers", title: "Speakers" },
    ...own.filter((tab) => tab.id !== "summary").map((tab) => ({ id: tab.id, title: tab.title, ext: tab })),
  ];
}

function recordingTabAvailable(tab, item) {
  if (!tab.available) return true;
  try {
    return Boolean(tab.available(item));
  } catch (err) {
    console.error("Recording tab check failed:", tab.id, err);
    return false;
  }
}

// Draws the tab row and the chosen panel. An extension tab's container is kept per recording, so a redraw puts
// the same element back: what the extension drew (and its state) survives.
function renderRecordingTabs(detail, item) {
  const tabs = recordingTabList(item);
  const active = tabs.some((t) => t.id === recChosenTab) ? recChosenTab : "transcript";
  const row = detail.querySelector(".rec-tabs");
  const focused = row.contains(document.activeElement) ? document.activeElement.dataset.tab : null;
  const buttons = tabs.map((t) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rec-tab";
    button.id = "rec-tab-" + t.id;
    button.dataset.tab = t.id;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(t.id === active));
    button.setAttribute("aria-controls", "rec-panel");
    button.tabIndex = t.id === active ? 0 : -1;
    button.append(t.title);
    if (t.pro) {
      const label = document.createElement("span");
      label.className = "pro-label";
      label.textContent = "Pro";
      button.append(label);
    }
    button.addEventListener("click", () => chooseRecordingTab(t.id));
    button.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const at = tabs.indexOf(t);
      const next = tabs[(at + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      chooseRecordingTab(next.id);
      const moved = document.getElementById("rec-tab-" + next.id);
      if (moved) moved.focus();
    });
    return button;
  });
  row.replaceChildren(...buttons);
  const again = focused && buttons.find((b) => b.dataset.tab === focused);
  if (again) again.focus();

  const tab = tabs.find((t) => t.id === active);
  const panel = document.createElement("div");
  panel.className = "rec-panel";
  panel.id = "rec-panel";
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", "rec-tab-" + active);
  // An extension tab's kept container moves into the new panel before the old panel is dropped.
  if (tab.ext) panel.append(recordingTabContainer(tab.ext, item));
  else if (tab.pro) panel.append(proSummaryNote());
  else if (active === "speakers") panel.append(speakersPanel(detail, item));
  else panel.append(transcriptPanel(detail, item));
  detail.querySelector(".rec-panels").replaceChildren(panel);
  if (active === "transcript") applyTranscriptMark(detail);
}

function chooseRecordingTab(id) {
  const detail = currentDetail();
  const tabs = detail ? recordingTabList(detail._item) : [];
  if (detail && !tabs.some((t) => t.id === id)) return false;
  recChosenTab = id;
  if (detail) renderRecordingTabs(detail, detail._item);
  return true;
}

function proSummaryNote() {
  const note = document.createElement("div");
  note.className = "rec-pro-note";
  note.innerHTML = `
    <h3>Summaries come with NexusVox Pro <span class="pro-label">Pro</span></h3>
    <p>Pro writes a short summary of each meeting and upload on your own machine. Every point links back to the
      passage in the transcript it comes from, so you can check it in one click.</p>
    <a class="rec-pro-link" href="${PRO_URL}" target="_blank" rel="noopener">See what Pro adds</a>`;
  return note;
}

// ── Transcript ───────────────────────────────────────────────────────
// "Speaker 1: ..." paragraphs become timed lines: the time comes from the paragraph's speaker turn when the
// paragraphs and turns line up one to one; otherwise the lines go without times. A prefix is read as a speaker
// only for a meeting (always split by speaker) or a recording with speaker-labelled word timing, never because a
// plain text happens to have paragraphs such as "Agenda: ..." and "Note: ...".
const SPEAKER_PREFIX = /^([^:\n]{1,60}):\s+([\s\S]+)$/;

function transcriptLines(text, turns, kind) {
  const paragraphs = (text || "").split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const parsed = paragraphs.map((p) => {
    const match = SPEAKER_PREFIX.exec(p);
    return match ? { who: match[1].trim(), text: match[2] } : { who: null, text: p };
  });
  const timed = turns.length > 0 && turns.length === paragraphs.length;
  const labelled = parsed.length > 0 && parsed.every((p) => p.who) && (turns.length > 0 || kind === "meeting");
  const order = new Map();
  return parsed.map((p, i) => {
    const who = labelled ? p.who : null;
    if (who && !order.has(who)) order.set(who, order.size);
    return {
      who,
      colour: who ? order.get(who) % 4 : null,
      text: labelled ? p.text : paragraphs[i],
      start: timed ? turns[i].start : null,
      end: timed ? turns[i].end : null,
    };
  });
}

function transcriptPanel(detail, item) {
  const wrap = document.createElement("div");
  wrap.className = "rec-transcript";
  if (item.cleaned_text) {
    const bar = document.createElement("div");
    bar.className = "rec-transcript-bar";
    const original = recShowOriginal.has(String(item.id));
    bar.innerHTML = `<span>${original ? "As transcribed" : "Cleaned up"}</span>
      <button type="button" class="period-btn rec-view-switch">${original ? "Show cleaned" : "Show original"}</button>`;
    bar.querySelector(".rec-view-switch").addEventListener("click", () => {
      const key = String(item.id);
      if (recShowOriginal.has(key)) recShowOriginal.delete(key);
      else recShowOriginal.add(key);
      drawRecordingBody(detail, detail._item);
    });
    wrap.append(bar);
  }
  const lines = detail._lines || [];
  if (!lines.length) {
    wrap.insertAdjacentHTML("beforeend", `<p class="empty-state">This recording has no text.</p>`);
    return wrap;
  }
  const canSeek = typeof detail._seek === "function";
  const html = lines.map((line, i) => {
    const time = line.start === null ? "" : canSeek
      ? `<button type="button" class="rec-time" data-start="${line.start}" aria-label="Play from ${formatClock(Math.floor(line.start))}">${formatClock(Math.floor(line.start))}</button>`
      : `<span class="rec-time">${formatClock(Math.floor(line.start))}</span>`;
    const who = line.who ? `<div class="rec-who rec-who-${line.colour}">${escapeHtml(line.who)}</div>` : "";
    return `<div class="rec-line${line.start === null ? " untimed" : ""}" data-line="${i}">
      ${time}<div class="rec-line-body">${who}<p class="rec-text">${escapeHtml(line.text)}</p></div></div>`;
  }).join("");
  wrap.insertAdjacentHTML("beforeend", html);
  if (canSeek) {
    wrap.addEventListener("click", (e) => {
      const time = e.target.closest("button.rec-time");
      if (time) detail._seek(Number(time.dataset.start));
    });
  }
  return wrap;
}

// Marks the passage an extension asked for: the lines whose turn overlaps {start, end}, or the first line that
// holds {text}, with that text highlighted. The pane scrolls it into view once.
function applyTranscriptMark(detail) {
  if (!recMark || String(recMark.id) !== detail.dataset.id) return;
  const lines = detail._lines || [];
  const transcript = detail.querySelector(".rec-transcript");
  const nodes = transcript ? [...transcript.querySelectorAll(".rec-line")] : [];
  if (nodes.length !== lines.length) return;
  let first = null;
  if (typeof recMark.start === "number") {
    const end = typeof recMark.end === "number" ? recMark.end : recMark.start;
    lines.forEach((line, i) => {
      if (line.start === null) return;
      if (line.start <= end && (line.end === null ? line.start : line.end) >= recMark.start) {
        nodes[i].classList.add("rec-mark");
        first = first || nodes[i];
      }
    });
  }
  if (!first && recMark.text) {
    const needle = String(recMark.text).toLowerCase();
    const at = lines.findIndex((line) => line.text.toLowerCase().includes(needle));
    if (at >= 0) {
      const p = nodes[at].querySelector(".rec-text");
      const start = lines[at].text.toLowerCase().indexOf(needle);
      const mark = document.createElement("mark");
      mark.textContent = lines[at].text.slice(start, start + needle.length);
      p.replaceChildren(lines[at].text.slice(0, start), mark, lines[at].text.slice(start + needle.length));
      nodes[at].classList.add("rec-mark");
      first = nodes[at];
    }
  }
  if (first && !recMark.scrolled) {
    recMark.scrolled = true;
    first.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// ── Speakers ─────────────────────────────────────────────────────────
function speakersPanel(detail, item) {
  const wrap = document.createElement("div");
  wrap.className = "rec-speakers";
  const lines = (detail._lines || []).filter((l) => l.who);
  if (!lines.length) {
    wrap.innerHTML = `<p class="empty-state">No speakers in this transcript. Turn on <strong>Detect speakers</strong> in the
      upload dialog to label who said what; meetings are always split by speaker.</p>`;
    return wrap;
  }
  const speakers = new Map();
  for (const line of lines) {
    const s = speakers.get(line.who) || { who: line.who, colour: line.colour, turns: 0, seconds: 0 };
    s.turns += 1;
    if (line.start !== null && line.end !== null) s.seconds += Math.max(0, line.end - line.start);
    speakers.set(line.who, s);
  }
  const total = [...speakers.values()].reduce((sum, s) => sum + s.seconds, 0);
  wrap.innerHTML = `<ul class="rec-speaker-list">${[...speakers.values()].map((s) => {
    const share = total ? Math.round((s.seconds / total) * 100) : null;
    const talk = total ? `${formatRecordingLength(s.seconds * 1000)} · ${share}%` : "";
    return `<li class="rec-speaker">
      <span class="rec-who rec-who-${s.colour}">${escapeHtml(s.who)}</span>
      <span class="rec-speaker-meta">${s.turns} ${s.turns === 1 ? "turn" : "turns"}${talk ? " · " + talk : ""}</span>
      ${share === null ? "" : `<span class="rec-speaker-bar" aria-hidden="true"><span class="rec-speaker-fill rec-fill-${s.colour}" style="width: ${share}%"></span></span>`}
    </li>`;
  }).join("")}</ul>`;
  return wrap;
}

// ── Extension tabs ───────────────────────────────────────────────────
// NexusVoxExt.registerRecordingTab(id, {title, render(container, recording), available(recording)}).
function addRecordingTab(id, tab) {
  const valid = typeof id === "string" && /^[a-z0-9_-]+$/.test(id) && tab && typeof tab.render === "function";
  if (!valid || (id !== "summary" && CORE_RECORDING_TABS.includes(id)) || recordingTabs.some((t) => t.id === id)) {
    console.error("Recording tab not registered (invalid or taken id, or no render function):", id);
    return false;
  }
  recordingTabs.push({ id, title: String(tab.title || id), render: tab.render, available: tab.available });
  const detail = currentDetail();
  if (detail) renderRecordingTabs(detail, detail._item);
  return true;
}

function recordingTabContainer(tab, item) {
  const key = `${tab.id}:${item.id}`;
  let container = recTabContent.get(key);
  if (container) {
    recTabContent.delete(key); // to the end: the most recently shown
    recTabContent.set(key, container);
  } else {
    container = document.createElement("div");
    container.className = "rec-ext-tab";
    recTabContent.set(key, container);
    try {
      tab.render(container, item);
    } catch (err) {
      console.error("Recording tab failed:", tab.id, err);
      container.textContent = "This tab failed to load.";
    }
    trimRecordingTabContent(String(item.id));
  }
  return container;
}

function trimRecordingTabContent(shownId) {
  for (const key of [...recTabContent.keys()]) {
    if (recTabContent.size <= REC_TAB_KEEP) return;
    if (key.split(":")[1] !== shownId) recTabContent.delete(key);
  }
}

function refreshRecordingTabContent(id, recordingId) {
  for (const key of [...recTabContent.keys()]) {
    const [tabId, recId] = key.split(":");
    if (tabId === id && (recordingId === undefined || recordingId === null || recId === String(recordingId))) {
      recTabContent.delete(key);
    }
  }
  const detail = currentDetail();
  if (detail && recChosenTab === id) renderRecordingTabs(detail, detail._item);
}

// NexusVoxExt.showTranscript(id, {mark}): the recording in the detail pane, on its Transcript tab.
// The recording is looked up in a list of its own fetching, before anything on screen changes: an unknown id
// (deleted, or older than the listed rows) leaves the user where they are and resolves false.
async function showRecording(id, options = {}) {
  let rows;
  try {
    rows = await fetchRecordingRows();
  } catch (err) {
    console.error("Could not look the recording up:", err);
    return false;
  }
  const item = rows.find((i) => String(i.id) === String(id));
  if (!item) return false;
  // A step in the history (`restore`) can have been left again while the list was fetched.
  if (options && options.current && !options.current()) return false;
  const mark = options && options.mark;
  // Coming back to the recording that is already chosen keeps its tab and passage, as the user left them.
  if (!(options && options.restore && recSelectedId === String(item.id))) {
    recMark = mark ? { id: String(item.id), start: mark.start, end: mark.end, text: mark.text } : null;
    recChosenTab = "transcript";
  }
  recSelectedId = String(item.id);
  if (!matchesRecordingSearch(item, recSearch.value.trim().toLowerCase())) recSearch.value = "";
  applyRecordingRows(rows, false);
  // Its own history step; Back and Forward restore it (restoreRecording passes restore: true).
  if (!(options && options.restore)) pushPage("#recordings/" + encodeURIComponent(item.id));
  // Opening the tab starts a load of its own, which makes any load still in flight stale (it drops its answer),
  // and keeps this recording chosen. Until it answers, the rows fetched here are what the tab shows.
  activateTab("recordings", { keepScroll: true });
  const row = recList.querySelector(`.rec-row[data-id="${item.id}"]`);
  if (row) {
    row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    row.classList.remove("flash-highlight");
    void row.offsetWidth; // restart the animation when the link is used again
    row.classList.add("flash-highlight");
    setTimeout(() => row.classList.remove("flash-highlight"), 2000);
  }
  return true;
}
