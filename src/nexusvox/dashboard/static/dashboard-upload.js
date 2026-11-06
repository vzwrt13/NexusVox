/* NexusVox Dashboard — Upload tab: file upload, jobs, and the transcript cards the Meetings tab shares */

// ── File Upload (Upload Tab) ─────────────────────────────────────────
// Choosing or dropping a file only selects it; the Transcribe button starts the job, so the
// options (Detect speakers) can be set in between and are read at the click.
const uploadZone = document.getElementById("upload-zone");
const uploadInput = document.getElementById("upload-input");
const uploadBrowse = document.getElementById("upload-browse");
const uploadSelected = document.getElementById("upload-selected");
const uploadSelectedName = document.getElementById("upload-selected-name");
const uploadSelectedMeta = document.getElementById("upload-selected-meta");
const uploadRemoveBtn = document.getElementById("upload-remove");
const uploadZoneText = document.getElementById("upload-zone-text");
const uploadZoneReady = document.getElementById("upload-zone-ready");
const transcribeBtn = document.getElementById("upload-transcribe");
let selectedUploadFile = null;
let uploadRunning = false;

function updateTranscribeButton() {
  transcribeBtn.disabled = !selectedUploadFile || uploadRunning;
  uploadRemoveBtn.disabled = uploadRunning;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  const kb = Math.round(bytes / 1024);
  if (kb < 1024) return kb + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function formatUploadClock(seconds) {
  const s = Math.round(seconds);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

// Reads the duration from the file's metadata; best effort, the row simply stays without it.
// Only one probe lives at a time: a new selection or a removal cancels the running one.
let cancelUploadProbe = null;
function showUploadDuration(file, sizeText) {
  if (cancelUploadProbe) cancelUploadProbe();
  const url = URL.createObjectURL(file);
  const probe = new Audio();
  let timer = null;
  const done = () => {
    clearTimeout(timer);
    probe.removeAttribute("src");
    probe.load(); // drops the media resource; removing src alone does not abort the load
    URL.revokeObjectURL(url);
    if (cancelUploadProbe === done) cancelUploadProbe = null;
  };
  cancelUploadProbe = done;
  timer = setTimeout(done, 10000); // a stalled probe must not hold the blob forever
  probe.preload = "metadata";
  probe.addEventListener("loadedmetadata", () => {
    if (selectedUploadFile === file && Number.isFinite(probe.duration)) {
      uploadSelectedMeta.textContent = sizeText + " · " + formatUploadClock(probe.duration);
    }
    done();
  });
  probe.addEventListener("error", done);
  probe.src = url;
}

function selectUploadFile(file) {
  selectedUploadFile = file;
  uploadSelected.hidden = !file;
  uploadSelectedName.textContent = file ? file.name : "";
  const sizeText = file ? formatFileSize(file.size) : "";
  uploadSelectedMeta.textContent = sizeText;
  uploadZone.classList.toggle("has-file", Boolean(file));
  uploadZoneText.hidden = Boolean(file);
  uploadZoneReady.hidden = !file;
  if (file) showUploadDuration(file, sizeText);
  else if (cancelUploadProbe) cancelUploadProbe();
  document.getElementById("upload-error").style.display = "none";
  document.getElementById("upload-note").style.display = "none";
  updateTranscribeButton();
}

uploadBrowse.addEventListener("click", (e) => {
  e.preventDefault();
  uploadInput.click();
});

document.getElementById("upload-browse-again").addEventListener("click", (e) => {
  e.preventDefault();
  uploadInput.click();
});

uploadRemoveBtn.addEventListener("click", () => {
  if (uploadRunning) return;
  selectUploadFile(null);
  uploadBrowse.focus(); // the row, and the button with it, is gone; keep keyboard focus nearby
});

uploadInput.addEventListener("change", () => {
  if (uploadInput.files.length && !uploadRunning) selectUploadFile(uploadInput.files[0]);
  uploadInput.value = ""; // so choosing the same file again still counts as a choice
});

transcribeBtn.addEventListener("click", () => {
  if (selectedUploadFile && !uploadRunning) handleFileUpload(selectedUploadFile);
});

uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadZone.classList.add("drag-over");
});

uploadZone.addEventListener("dragleave", () => {
  uploadZone.classList.remove("drag-over");
});

uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadZone.classList.remove("drag-over");
  if (e.dataTransfer.files.length && !uploadRunning) selectUploadFile(e.dataTransfer.files[0]);
});

const cleanupToggle = document.getElementById("upload-cleanup-toggle");
const cleanupLevel = document.getElementById("upload-cleanup-level");
function syncCleanupLevel() {
  cleanupLevel.disabled = !cleanupToggle.checked;
}
cleanupToggle.addEventListener("change", () => {
  syncCleanupLevel();
  refreshLlmHints();
  if (cleanupToggle.checked) loadLlmStatus().catch(() => {}); // up to date before the user starts
});
syncCleanupLevel(); // a reload can restore a checked toggle without a change event

const diarizeToggle = document.getElementById("upload-diarize-toggle");
const diarizeHint = document.getElementById("upload-diarize-hint");
const DIARIZE_HINT = diarizeHint.textContent;

// Speaker detection needs a model with a diarization endpoint; the toggle follows the active model.
// Checked when the tab opens, when the page becomes visible again and right before each upload,
// because the model can change elsewhere (another browser tab, the config file).
async function refreshDiarizeToggle() {
  let current = {};
  try {
    current = await fetch("/api/models/current").then((r) => r.json());
  } catch {
    current = {};
  }
  const supported = Boolean(current.diarization);
  diarizeToggle.disabled = !supported;
  if (!supported) diarizeToggle.checked = false;
  const models = (current.diarization_models || []).join(" or ") || "a model with speaker detection";
  diarizeHint.textContent = supported
    ? DIARIZE_HINT
    : current.diarization_hint || "Requires " + models + " as the active model.";
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && document.getElementById("tab-upload").classList.contains("active")) {
    refreshDiarizeToggle();
    resumeUploadJob();
  }
});

const UPLOAD_POLL_MS = 1000;
const uploadStatusEl = document.getElementById("upload-status");
const uploadStatusText = document.getElementById("upload-status-text");
const uploadProgressEl = document.getElementById("upload-progress");
const uploadProgressBar = document.getElementById("upload-progress-bar");
const uploadErrorEl = document.getElementById("upload-error");
const uploadNoteEl = document.getElementById("upload-note");
const meetingErrorEl = document.getElementById("meeting-error");
const meetingNoteEl = document.getElementById("meeting-note");

// The error and note boxes of the tab that holds `el`: a card in the Meetings tab reports there, never in the
// hidden Upload tab. Both tabs draw their cards with the same actions and exports.
function feedbackEls(el) {
  const inMeetings = !!(el && el.closest && el.closest("#tab-meetings"));
  return inMeetings ? { error: meetingErrorEl, note: meetingNoteEl } : { error: uploadErrorEl, note: uploadNoteEl };
}

function showUploadProgress(job) {
  uploadStatusEl.style.display = "flex";
  uploadProgressEl.style.display = "block";
  uploadStatusText.textContent = job.filename + ": " + job.message + "...";
  uploadProgressBar.value = Math.round(job.percent * 100);
}

function hideUploadProgress() {
  uploadStatusEl.style.display = "none";
  uploadProgressEl.style.display = "none";
}

function showUploadError(message, box = uploadErrorEl) {
  box.textContent = message;
  box.style.display = "block";
}

async function handleFileUpload(file) {
  // Read at the click. Asked for with a model that cannot do it (changed elsewhere): still sent,
  // so the job's note says why.
  const diarize = diarizeToggle.checked;
  const cleanup = cleanupToggle.checked ? cleanupLevel.value : "";
  uploadRunning = true;
  updateTranscribeButton();
  uploadErrorEl.style.display = "none";
  uploadNoteEl.style.display = "none";
  uploadStatusEl.style.display = "flex";
  uploadStatusText.textContent = "Uploading " + file.name + "...";
  await refreshDiarizeToggle();

  const formData = new FormData();
  formData.append("file", file);
  if (diarize) formData.append("diarize", "1");
  if (cleanup) formData.append("cleanup", cleanup);

  try {
    const res = await fetch("/api/file-transcribe", { method: "POST", body: formData });
    const data = await res.json();
    if (!data.ok) {
      hideUploadProgress();
      showUploadError(data.error || "Upload failed");
      return;
    }
    await trackUploadJob(data.job_id, file);
  } catch (err) {
    hideUploadProgress();
    showUploadError("Network error: " + err.message);
  } finally {
    uploadRunning = false;
    updateTranscribeButton();
  }
}

// GET a JSON endpoint; a non-JSON or failed reply (a proxy's HTML 5xx, say) throws instead of passing as data.
async function fetchJobJson(url) {
  const res = await fetch(url);
  let body = null;
  try {
    body = await res.json();
  } catch {
    throw new Error("HTTP " + res.status);
  }
  return { ok: res.ok, status: res.status, body };
}

const UPLOAD_POLL_RETRIES = 5; // failed polls in a row before giving up on a job

// Polls a job until it ends, then shows how it ended. `file` is the one selected for it (null when the
// job was picked up again after the page was reopened). A poll that fails is tried again a few times;
// when the dashboard stays unreachable the job is left running on the server and the page says so.
async function trackUploadJob(jobId, file) {
  uploadRunning = true;
  updateTranscribeButton();
  let failures = 0;
  try {
    for (;;) {
      let reply;
      try {
        reply = await fetchJobJson("/api/file-transcribe/" + jobId);
        failures = 0;
      } catch (err) {
        if (++failures >= UPLOAD_POLL_RETRIES) {
          hideUploadProgress();
          showUploadError("Lost contact with the dashboard (" + err.message + "). The file may still be transcribing; reopen this tab to check.");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, UPLOAD_POLL_MS));
        continue;
      }
      if (!reply.ok) {
        hideUploadProgress();
        showUploadError(reply.body.error || "Lost track of the transcription");
        return;
      }
      if (reply.body.state !== "running") {
        showUploadOutcome(reply.body, file);
        return;
      }
      showUploadProgress(reply.body);
      await new Promise((resolve) => setTimeout(resolve, UPLOAD_POLL_MS));
    }
  } finally {
    uploadRunning = false;
    updateTranscribeButton();
  }
}

function showUploadOutcome(job, file) {
  hideUploadProgress();
  if (job.state === "error") {
    showUploadError(job.error || "Transcription failed");
    return; // a failed file stays selected so it can be tried again
  }
  // Done with this file.
  if (file && selectedUploadFile === file) selectUploadFile(null);
  const notes = (job.result && job.result.notes) || [];
  if (notes.length) {
    uploadNoteEl.textContent = "Saved, with a problem: " + notes.join(" ");
    uploadNoteEl.style.display = "block";
  }
  loadFileTranscriptions();
}

// A job keeps running on the server when the page is closed or reloaded, and another browser tab can start
// one; the Upload tab picks it up when it opens and then keeps looking while it is shown.
async function resumeUploadJob() {
  if (uploadRunning) return;
  let job = null;
  try {
    const reply = await fetchJobJson("/api/file-transcribe/current");
    job = reply.ok ? reply.body.job : null;
  } catch {
    return; // the next look tries again
  }
  if (!job || uploadRunning) return;
  uploadErrorEl.style.display = "none";
  uploadNoteEl.style.display = "none";
  if (job.state === "running") {
    showUploadProgress(job);
    await trackUploadJob(job.job_id, null);
  } else {
    showUploadOutcome(job, null);
  }
}

const UPLOAD_WATCH_MS = 3000;

function uploadTabShown() {
  return document.visibilityState === "visible" && document.getElementById("tab-upload").classList.contains("active");
}

setInterval(() => {
  if (uploadTabShown()) resumeUploadJob();
}, UPLOAD_WATCH_MS);

// An export is fetched, not followed as a link, so a failing one shows its message instead of
// saving the error reply as a file.
async function downloadExport(url, source) {
  const { error: errorBox } = feedbackEls(source);
  errorBox.style.display = "none";
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showUploadError(body.error || `Export failed (${res.status})`, errorBox);
      return;
    }
    const disposition = res.headers.get("Content-Disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/);
    const plain = disposition.match(/filename="([^"]*)"/);
    const name = encoded ? decodeURIComponent(encoded[1]) : plain ? plain[1] : "export";
    const blobUrl = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = name;
    a.click();
    URL.revokeObjectURL(blobUrl);
  } catch (err) {
    showUploadError(`Export failed: ${err.message}`, errorBox);
  }
}

// An upload action either POSTs to its endpoint or calls a handler its panel script registered.
// Running actions are remembered so a rebuilt list does not offer the button again meanwhile.
const runningUploadActions = new Set();

async function runUploadAction(button, item) {
  const action = extensionUploadActions.find((a) => a.id === button.dataset.action);
  if (!action) return;
  const key = `${action.id}:${item.id}`;
  if (runningUploadActions.has(key)) return;
  runningUploadActions.add(key);
  const { error: errorBox, note: noteBox } = feedbackEls(button);
  errorBox.style.display = "none";
  noteBox.style.display = "none";
  // The row's main button and its Retry run the same action: both go quiet meanwhile.
  const card = button.closest(".flagged-card");
  const siblings = (card ? [...card.querySelectorAll(".upload-action, .upload-retry")] : [button])
    .filter((b) => b.dataset.action === action.id && !b.disabled);
  siblings.forEach((b) => { b.disabled = true; });
  try {
    if (action.endpoint) {
      const res = await fetch(action.endpoint.replaceAll("{id}", item.id), { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || body.ok === false) {
        showUploadError(body.error || `${action.label} failed (${res.status})`, errorBox);
      } else if (body.note || body.message) {
        noteBox.textContent = body.note || body.message;
        noteBox.style.display = "block";
      }
    } else {
      if (!uploadActionHandlers[action.id]) {
        openExtensionPanel(action.panel);
        const panel = extensionPanels[action.panel];
        if (!panel) throw new Error(`panel "${action.panel}" does not exist`);
        if (!(await panel.ready)) throw new Error("its script could not be loaded");
      }
      const handler = uploadActionHandlers[action.id];
      if (!handler) throw new Error("its script did not register a handler");
      await handler(item, button);
    }
  } catch (err) {
    showUploadError(`${action.label} failed: ${err.message}`, errorBox);
  } finally {
    runningUploadActions.delete(key);
    siblings.forEach((b) => { b.disabled = false; });
    // Also after a failure: the action may just have become unavailable (an expired licence).
    loadFileTranscriptions();
  }
}

// ── Pending and failed states ────────────────────────────────────────
// An extension's `available` check may answer a string (greyed out, the text is the tooltip) or a dict:
// `{pending: label}` (spinner, the list keeps refreshing) or `{error: reason, retry: action id}`.
const PENDING_POLL_MS = 4000;

function availabilityState(reason) {
  if (reason && typeof reason === "object") {
    if (typeof reason.pending === "string") return { kind: "pending", text: reason.pending };
    if (typeof reason.error === "string") return { kind: "error", text: reason.error, retry: reason.retry || "" };
    return { kind: "off", text: "Not available" };
  }
  return { kind: "off", text: String(reason) };
}

function rowsHavePending(rows) {
  return rows.some((row) => [row.exports_unavailable, row.actions_unavailable].some(
    (states) => Object.values(states || {}).some((reason) => availabilityState(reason).kind === "pending")));
}

// Reloads the lists after `intervalMs` while the last load had a pending row: one timer at most, none once nothing is pending.
// The default timers are wrapped: a browser's setTimeout throws "Illegal invocation" when called as a method of `timers`.
function createPendingPoller(reload, intervalMs,
  timers = { set: (fn, ms) => setTimeout(fn, ms), clear: (handle) => clearTimeout(handle) }) {
  let handle = null;
  return {
    update(pending) {
      if (!pending) {
        if (handle !== null) timers.clear(handle);
        handle = null;
      } else if (handle === null) {
        handle = timers.set(() => { handle = null; reload(); }, intervalMs);
      }
    },
    get active() { return handle !== null; },
  };
}
// ── end of pending and failed states ──────────────────────────────────

// The history lists. A poll fetches only the lists that last had a pending row and are on screen, and a render
// touches only the cards that changed, so audio playback, an open delete confirmation or "Show original" on
// the other cards survive it.
const FILE_LISTS = [
  { id: "upload-list", kind: "", empty: "No file transcriptions yet." },
  { id: "meeting-list", kind: "meeting", empty: "No meetings yet." },
];
const pendingLists = new Set(); // ids of the lists whose last load had a pending row
let loadGeneration = 0; // a load that is no longer the newest drops its answer, so an old snapshot never wins

const pendingPoller = createPendingPoller(() => {
  if (document.hidden) return; // the visibilitychange handler below picks it up again
  loadFileTranscriptions(true);
}, PENDING_POLL_MS);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && pendingLists.size && !pendingPoller.active) loadFileTranscriptions(true);
});

function listIsShown(list) {
  const el = document.getElementById(list.id);
  return !!el && !!el.closest(".tab-content.active");
}

// Never rejects: a failed refresh keeps the polling going (or leaves it off) as it was.
async function loadFileTranscriptions(poll = false) {
  const generation = ++loadGeneration;
  const lists = poll ? FILE_LISTS.filter((l) => pendingLists.has(l.id) && listIsShown(l)) : FILE_LISTS;
  try {
    const [rows] = await Promise.all([
      Promise.all(lists.map((l) => fetch("/api/file-transcriptions" + (l.kind ? `?kind=${l.kind}` : "")).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }))),
      poll ? null : loadPendingMeetings(),
    ]);
    if (generation !== loadGeneration) return;
    lists.forEach((list, i) => {
      renderFileTranscriptions(document.getElementById(list.id), rows[i], list.empty, poll);
      if (rowsHavePending(rows[i])) pendingLists.add(list.id);
      else pendingLists.delete(list.id);
    });
    // A list that is not on screen is not polled; opening its tab loads it again, so it must not keep the timer alive.
    if (poll) FILE_LISTS.filter((l) => !lists.includes(l) && !listIsShown(l)).forEach((l) => pendingLists.delete(l.id));
  } catch (err) {
    console.error("Failed to load file transcriptions:", err);
  }
  if (generation === loadGeneration) pendingPoller.update(pendingLists.size > 0);
}

// Jump to one transcript card: load the lists, open the tab that holds the card, scroll it in and flash it.
async function showTranscriptCard(id) {
  await loadFileTranscriptions();
  const selector = `> .flagged-card[data-id="${String(id).replace(/"/g, "")}"]`;
  const inMeetings = document.querySelector("#meeting-list " + selector);
  const card = inMeetings || document.querySelector("#upload-list " + selector);
  if (!card) return false;
  const tab = inMeetings ? "meetings" : "upload";
  history.replaceState(null, "", "#" + tab);
  activateTab(tab, { keepScroll: true });
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.classList.remove("flash-highlight");
  void card.offsetWidth; // restart the animation when the link is used again
  card.classList.add("flash-highlight");
  setTimeout(() => card.classList.remove("flash-highlight"), 2000);
  return true;
}

// One history list: `kind` "" shows every entry, "meeting" only meetings. Each card can be deleted, with the
// confirmation inside the card (never a browser dialog). A `poll` keeps every card whose data did not change.
function renderFileTranscriptions(container, data, emptyText, poll) {
  if (!data.length) {
    container.innerHTML = `<p class="empty-state">${escapeHtml(emptyText)}</p>`;
    return;
  }
  container.querySelectorAll(":scope > :not(.flagged-card)").forEach((el) => el.remove());
  const existing = new Map([...container.querySelectorAll(":scope > .flagged-card")].map((c) => [c.dataset.id, c]));
  const wanted = new Set();
  data.forEach((item, i) => {
    const signature = cardSignature(item);
    let card = existing.get(String(item.id));
    if (card && (card._editing || (poll && card._signature === signature))) {
      card._item = item;  // a card with an open rename field keeps it, whatever the refresh
    } else {
      const fresh = buildFileCard(item, signature);
      if (card) card.replaceWith(fresh);
      card = fresh;
    }
    wanted.add(card);
    if (container.children[i] !== card) container.insertBefore(card, container.children[i] || null);
  });
  container.querySelectorAll(":scope > .flagged-card").forEach((c) => {
    if (!wanted.has(c)) c.remove();
  });
}

// What a card shows besides the (large) transcript text, whose length stands in for it.
function cardSignature(item) {
  const running = extensionUploadActions.filter((a) => runningUploadActions.has(`${a.id}:${item.id}`)).map((a) => a.id);
  return JSON.stringify([item.id, item.exports_unavailable, item.actions_unavailable, running, item.has_audio,
    item.original_filename, item.title, item.language, item.model, (item.text || "").length, (item.cleaned_text || "").length]);
}

function buildFileCard(item, signature) {
  const template = document.createElement("template");
  template.innerHTML = fileCardHtml(item).trim();
  const card = template.content.firstElementChild;
  card._item = item;
  card._signature = signature;

  card.querySelectorAll(".upload-export").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (link.classList.contains("disabled")) return;
      downloadExport(link.href, link);
    });
  });

  card.querySelectorAll(".upload-action, .upload-retry").forEach((button) => {
    button.addEventListener("click", () => runUploadAction(button, card._item));
  });

  // A cleaned upload opens on the cleaned text; the button flips it to the transcript as transcribed.
  card.querySelectorAll(".upload-view-switch").forEach((button) => {
    button.addEventListener("click", () => {
      const showingOriginal = button.textContent === "Show cleaned";
      button.textContent = showingOriginal ? "Show original" : "Show cleaned";
      card.querySelector(".upload-text").textContent = showingOriginal ? card._item.cleaned_text : card._item.text;
    });
  });

  card.querySelectorAll(".upload-delete-btn").forEach((button) => {
    button.addEventListener("click", () => askDeleteFileTranscription(button.closest(".upload-delete-row")));
  });

  card.querySelector(".upload-rename").addEventListener("click", () => editFileTitle(card));
  return card;
}

// Swaps the card's name for a text field: Enter saves, Esc or leaving the field cancels. An empty title brings
// the file or folder name back. The server keeps `original_filename`; only the title changes. Only a saved
// rename reloads the lists; cancelling puts the name back in place, so other cards keep their state.
function editFileTitle(card) {
  const row = card.querySelector(".upload-filename");
  if (card._editing) return;
  const item = card._item;
  const current = item.title || item.original_filename;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "upload-rename-input";
  input.maxLength = 255;
  input.value = current;
  input.setAttribute("aria-label", "Name");
  const nameHtml = row.innerHTML;
  card._editing = true;
  row.replaceChildren(input);
  input.focus();
  input.select();
  const restore = (error) => {
    card._editing = false;
    row.innerHTML = nameHtml;
    row.querySelector(".upload-rename").addEventListener("click", () => editFileTitle(card));
    if (error) {
      const note = document.createElement("span");
      setStatus(note, "down", "✕ " + error);
      row.append(note);
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
    card._editing = false;
    loadFileTranscriptions();
  };
  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return;  // Enter confirms an IME composition, it is not a save
    if (event.key === "Enter") finish(true);
    else if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(false));
}

function fileCardHtml(item) {
  return `
    <div class="flagged-card" data-id="${item.id}">
      <div class="flagged-meta">
        <span>${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span>
        <span class="flagged-lang">${(item.language || "").toUpperCase()}</span>
        <span class="upload-model">${escapeHtml(item.model || "")}</span>
        <span class="upload-duration">${formatDuration(item.duration_ms)}</span>
        ${item.cleaned_text ? `<button type="button" class="upload-view-switch">Show original</button>` : ""}
        ${extensionExports.map((t) => {
          const reason = (item.exports_unavailable || {})[t.id];
          if (reason !== undefined) return `<a class="upload-export disabled" aria-disabled="true" title="${escapeHtml(availabilityState(reason).text)}">${escapeHtml(t.label)}</a>`;
          return `<a class="upload-export" href="/api/file-transcriptions/${item.id}/export/${encodeURIComponent(t.id)}">${escapeHtml(t.label)}</a>`;
        }).join("")}
        ${extensionUploadActions.map((a) => {
          const reason = (item.actions_unavailable || {})[a.id];
          const off = reason !== undefined || runningUploadActions.has(`${a.id}:${item.id}`);
          return `<button type="button" class="upload-action" data-action="${escapeHtml(a.id)}"${off ? " disabled" : ""}${reason !== undefined ? ` title="${escapeHtml(availabilityState(reason).text)}"` : ""}>${escapeHtml(a.label)}</button>`;
        }).join("")}
      </div>
      ${renderRowStates(item)}
      <div class="upload-filename" data-kind="${escapeHtml(item.kind || "upload")}">
        <span class="upload-name">${escapeHtml(item.title || item.original_filename)}${item.kind === "meeting" ? " (meeting)" : ""}</span>
        <button type="button" class="upload-rename" title="Rename" aria-label="Rename">✎</button>
      </div>
      ${item.has_audio ? `<audio class="upload-audio" controls preload="none" src="/api/file-transcriptions/${item.id}/audio"></audio>` : ""}
      <div class="flagged-original upload-text">${escapeHtml(item.cleaned_text || item.text)}</div>
      <div class="upload-delete-row"><button type="button" class="period-btn upload-delete-btn">Delete</button></div>
    </div>
  `;
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

function askDeleteFileTranscription(row) {
  const card = row.closest(".flagged-card");
  const kind = card.querySelector(".upload-filename").dataset.kind === "meeting" ? "meeting and its audio files" : "entry and its audio";
  row.innerHTML = `
    <span class="upload-delete-ask">Delete this ${kind}? This cannot be undone.</span>
    <button type="button" class="period-btn upload-delete-btn upload-delete-confirm">Delete</button>
    <button type="button" class="period-btn upload-delete-cancel">Cancel</button>
    <span class="upload-delete-error" hidden></span>`;
  row.querySelector(".upload-delete-cancel").addEventListener("click", () => {
    row.innerHTML = '<button type="button" class="period-btn upload-delete-btn">Delete</button>';
    row.querySelector(".upload-delete-btn").addEventListener("click", () => askDeleteFileTranscription(row));
  });
  row.querySelector(".upload-delete-confirm").addEventListener("click", async (event) => {
    event.target.disabled = true;
    const resp = await fetch(`/api/file-transcriptions/${card.dataset.id}`, { method: "DELETE" });
    const result = await resp.json();
    if (!resp.ok || !result.ok) {
      const err = row.querySelector(".upload-delete-error");
      setStatus(err, "down", "✕ " + (result.error || "Delete failed"));
      err.hidden = false;
      event.target.disabled = false;
      return;
    }
    loadFileTranscriptions();
  });
}
