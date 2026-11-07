/* NexusVox Dashboard — Upload dialog of the Recordings tab: file choice, jobs, exports and actions */

// ── File Upload (dialog in the Recordings tab) ───────────────────────
// Choosing or dropping a file only selects it; the Transcribe button starts the job, so the
// options (Detect speakers) can be set in between and are read at the click. A file dropped anywhere
// on the Recordings tab opens the dialog with it.
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
const uploadDialog = document.getElementById("upload-dialog");
const recordingsTab = document.getElementById("tab-recordings");
const recDropOverlay = document.getElementById("rec-drop-overlay");
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

// The dialog closes as the job starts; its progress shows in the list pane, which stays in view.
transcribeBtn.addEventListener("click", () => {
  if (!selectedUploadFile || uploadRunning) return;
  if (uploadDialog.open) uploadDialog.close();
  handleFileUpload(selectedUploadFile);
});

function openUploadDialog() {
  refreshDiarizeToggle();
  loadLlmStatus().catch(() => {}); // the cleanup hint must reflect Ollama as it is now
  if (!uploadDialog.open) uploadDialog.showModal();
}

document.getElementById("upload-open").addEventListener("click", openUploadDialog);

// The zone's own handlers stop the event: the tab below would otherwise take the same drop a second time.
uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  e.stopPropagation();
  uploadZone.classList.add("drag-over");
});

uploadZone.addEventListener("dragleave", () => {
  uploadZone.classList.remove("drag-over");
});

uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  e.stopPropagation();
  uploadZone.classList.remove("drag-over");
  if (e.dataTransfer.files.length && !uploadRunning) selectUploadFile(e.dataTransfer.files[0]);
});

// Drag and drop onto the whole tab: an overlay while a file is over it, the dialog with the file on the drop.
// dragenter and dragleave fire for every child, so a depth count tells when the file has really left the tab.
let recDragDepth = 0;
const draggingFiles = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes("Files");
function hideDropOverlay() {
  recDragDepth = 0;
  recDropOverlay.hidden = true;
}
recordingsTab.addEventListener("dragenter", (e) => {
  if (!draggingFiles(e) || uploadDialog.open) return;
  e.preventDefault();
  recDragDepth += 1;
  recDropOverlay.hidden = false;
});
recordingsTab.addEventListener("dragover", (e) => {
  if (draggingFiles(e)) e.preventDefault(); // without it the browser opens the file instead of dropping it here
});
recordingsTab.addEventListener("dragleave", () => {
  recDragDepth -= 1;
  if (recDragDepth <= 0) hideDropOverlay();
});
recordingsTab.addEventListener("drop", (e) => {
  e.preventDefault();
  hideDropOverlay();
  if (!e.dataTransfer.files.length) return;
  if (uploadRunning) {
    showUploadError("A file is still being transcribed. Drop the next one when it is done.");
    return;
  }
  selectUploadFile(e.dataTransfer.files[0]);
  openUploadDialog();
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
  if (document.visibilityState === "visible" && recordingsTab.classList.contains("active")) {
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

// The error and note boxes for `el`: an export or action of the recording in the detail pane reports there,
// next to the recording it was run on; anything else (the upload job) in the list pane.
function feedbackEls(el) {
  const detail = el && el.closest ? el.closest(".rec-detail") : null;
  if (detail) return { error: detail.querySelector(".rec-detail-error"), note: detail.querySelector(".rec-detail-note") };
  return { error: uploadErrorEl, note: uploadNoteEl };
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
  // Done with this file: the new recording opens in the detail pane.
  if (file && selectedUploadFile === file) selectUploadFile(null);
  const notes = (job.result && job.result.notes) || [];
  if (notes.length) {
    uploadNoteEl.textContent = "Saved, with a problem: " + notes.join(" ");
    uploadNoteEl.style.display = "block";
  }
  const savedId = job.result && job.result.id;
  if (file && savedId !== undefined && savedId !== null) recSelectedId = String(savedId);
  loadFileTranscriptions();
}

// A job keeps running on the server when the page is closed or reloaded, and another browser tab can start
// one; the Recordings tab picks it up when it opens and then keeps looking while it is shown.
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
  return document.visibilityState === "visible" && recordingsTab.classList.contains("active");
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
  // The menu item and the Retry of a failed state run the same action: both go quiet meanwhile.
  const card = button.closest(".rec-detail");
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
