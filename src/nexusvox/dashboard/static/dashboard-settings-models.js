/* NexusVox Dashboard — Settings tab, Models group: language model, Ollama hints, transcription model */

// ── Ollama install hint (pure helpers, run under Node in tests) ──────
// Free 1.0 bundles no language model runtime: dictation works without one, Cleanup, Polish and upload cleanup
// need Ollama. `ollamaHint` turns /api/settings/llm-status into the hint every area shows; null = nothing to say.
const OLLAMA_DOWNLOAD_URL = "https://ollama.com/download";
function isLocalLlmUrl(url) {
  try {
    return ["127.0.0.1", "localhost", "[::1]"].includes(new URL(url).hostname);
  } catch {
    return false;
  }
}
// `here`: the hint sits in the Models card, next to the download button.
function ollamaHint(s, { here = false } = {}) {
  if (!s.reachable) {
    if (s.protocol && s.protocol !== "ollama") {
      return { state: "down", text: `○ No model server at ${s.url} — Cleanup and Polish are off until it answers.`, link: false };
    }
    if (!isLocalLlmUrl(s.url)) {
      return {
        state: "down",
        text: `○ No Ollama server answers at ${s.url}. Check that it is running and that [llm].url in config.toml is right.${here ? " The saved model is kept." : ""}`,
        link: false,
      };
    }
    return {
      state: "down",
      text: `○ Ollama is not running at ${s.url}. Cleanup, Polish and upload cleanup need it; dictation works without.${here ? " The saved model is kept." : ""} Install and start Ollama: `,
      link: true,
    };
  }
  if (s.model_installed === false) {
    return {
      state: "down",
      text: `○ Ollama is running, but model ${s.model} is not installed. ${here ? "Download it with the button below." : "Open Settings > Models to download it."}`,
      link: false,
    };
  }
  return null;
}
function hideHint(el) {
  setStatus(el, undefined, "");
  el.hidden = true;
}
// Writes the hint through setStatus() and appends the download link; clears and hides the element when all is well.
function renderOllamaHint(el, s, visible = true, opts = {}) {
  const hint = visible ? ollamaHint(s, opts) : null;
  if (!hint) {
    hideHint(el);
    return;
  }
  setStatus(el, hint.state, hint.text);
  if (hint.link) {
    const a = document.createElement("a");
    a.href = OLLAMA_DOWNLOAD_URL;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "ollama.com/download";
    el.append(a);
  }
  el.hidden = false;
}
// The Models card's own line: it also knows whether the model list could be read (`data` = /api/settings/llm-models).
function renderModelsHint(el, s, data) {
  if (!data) return hideHint(el);
  if (data.reachable) return renderOllamaHint(el, s, true, { here: true });
  if (data.protocol !== "ollama") {
    setStatus(el, "warn", "This server type has no model list. Set [llm].model in config.toml.");
  } else if (s.reachable) {
    // The server answers but the model list does not come back: not a missing install.
    setStatus(el, "down", `○ Ollama answers at ${s.url}, but the installed models cannot be listed. The saved model is kept.`);
  } else {
    return renderOllamaHint(el, s, true, { here: true });
  }
  el.hidden = false;
}
// All areas at once from one llm-status reply. `ui` = the elements and whether each feature is wanted, so the red
// hint only appears where the user turned the feature on (dictation alone never looks broken).
function renderLlmHints(s, ui) {
  if (!s) return;
  if (!ui.polishOn) hideHint(ui.polish);
  else if (ollamaHint(s)) renderOllamaHint(ui.polish, s);
  else {
    setStatus(ui.polish, "ok", `● Model ready at ${s.url} (model ${s.model})`);
    ui.polish.hidden = false;
  }
  renderOllamaHint(ui.cleanup, s, ui.cleanupOn);
  renderOllamaHint(ui.upload, s, ui.uploadOn);
  renderModelsHint(ui.models, s, ui.modelsData);
}
// ── end of Ollama install hint ───────────────────────────────────────

let lastLlmStatus = null;
let lastLlmModels = null;
function refreshLlmHints() {
  renderLlmHints(lastLlmStatus, {
    polish: llmStatus,
    cleanup: cleanupLlmHint,
    upload: uploadLlmHint,
    models: llmModelHint,
    polishOn: polishKeySelect.value !== "",
    cleanupOn: cleanupLevelSelect.value !== "off",
    uploadOn: document.getElementById("upload-cleanup-toggle").checked,
    modelsData: lastLlmModels,
  });
}

async function loadLlmStatus() {
  lastLlmStatus = await api("/api/settings/llm-status");
  refreshLlmHints();
}

// The hints have their own poll, independent of the model list: that one is skipped when its payload is unchanged
// or its card is hidden. Runs while a tab with a hint is open and the page is visible.
setInterval(() => {
  if (document.hidden) return;
  const open = ["tab-settings", "tab-upload"].some((id) => document.getElementById(id).classList.contains("active"));
  if (open) loadLlmStatus().catch(() => {});
}, 5000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadLlmStatus().catch(() => {});
});

// ── Language model dropdown ──────────────────────────────────────────
const llmModelHint = document.getElementById("llm-model-hint");
const llmSlowHint = document.getElementById("llm-slow-hint");
const llmPullBox = document.getElementById("llm-pull-status");
const llmPullText = document.getElementById("llm-pull-text");
const llmPullProgress = document.getElementById("llm-pull-progress");
const llmPullCancel = document.getElementById("llm-pull-cancel");
const llmPullSelected = document.getElementById("llm-pull-selected");
let llmSavedModel = "";
let llmPullTimer = null;

// ── LLM card refresh (pure helpers, run under Node in tests) ─────────
const LLM_REFRESH_MS = 5000;

// Where the model runs, from what Ollama reports for a loaded model (`/api/ps`); otherwise from the hardware.
// Figures Ollama under-reports (`plausible: false`) are never shown as real, and never claim "fully in VRAM".
function llmRunsOn(data) {
  const l = data.loaded;
  if (!l) return { text: data.device === "gpu" ? "Not loaded (will use the GPU if it fits)" : "Not loaded (no CUDA GPU, will use the CPU)", warn: false };
  if (!l.plausible) return { text: l.on_gpu ? "GPU" : "Loaded (placement unknown)", warn: false };
  if (l.ram_gb === 0) return { text: "GPU (fully in VRAM)", warn: false };
  if (l.vram_gb === 0) return { text: "CPU (in RAM)", warn: true };
  return { text: `GPU + CPU (${Math.round((100 * l.vram_gb) / (l.vram_gb + l.ram_gb))}% on the GPU)`, warn: true };
}

// Memory the model needs: Ollama's real split when it is loaded and plausible, else an estimate from the size on disk.
function llmMemory(data, m) {
  if (!data.reachable || data.protocol !== "ollama") return "—";
  const l = data.loaded;
  if (l && l.plausible) {
    const parts = [];
    if (l.vram_gb > 0) parts.push(`${l.vram_gb} GB VRAM`);
    if (l.ram_gb > 0) parts.push(`${l.ram_gb} GB RAM`);
    return parts.join(" + ") || "—";
  }
  if (!m || !m.size_gb) return "—";
  return `~${m.size_gb} GB ${data.device === "gpu" ? "VRAM" : "RAM"} (estimate)`;
}

// What the dropdown's options depend on: a change here (and only here) rebuilds the <select>.
function llmOptionsKey(data) {
  return JSON.stringify([data.reachable, data.protocol, data.selected, data.recommended, data.other]);
}

// Refreshes the card while it is on screen and the page is visible. `fetchData()` returns the models payload,
// `render(data, rebuildOptions)` draws it. Nothing is drawn when the payload did not change, and the options are
// rebuilt only when they changed (an open dropdown stays open); `force` (after a save or a pull) always draws.
function createLlmRefresher({ fetchData, render, isActive, schedule, cancel }) {
  let timer = null;
  let started = false;
  let lastData = null;
  let lastOptions = null;
  async function refresh(force = false) {
    let data;
    try {
      data = await fetchData();
    } catch {
      return false;
    }
    const snapshot = JSON.stringify(data);
    if (!force && snapshot === lastData) return false;
    const options = llmOptionsKey(data);
    render(data, force || options !== lastOptions);
    lastData = snapshot;
    lastOptions = options;
    return true;
  }
  function tick() {
    if (isActive()) refresh();
  }
  return {
    refresh,
    start() {
      started = true;
      if (timer === null) timer = schedule(tick, LLM_REFRESH_MS);
    },
    stop() {
      if (timer !== null) cancel(timer);
      timer = null;
    },
    get started() {
      return started;
    },
    get running() {
      return timer !== null;
    },
    // The page became visible again (or the card was opened): refresh once.
    wake() {
      tick();
    },
  };
}
// ── end of LLM card refresh ──────────────────────────────────────────

function renderLlmOverview(data, m) {
  const installed = !m || m.installed === null ? "Unknown (server not reachable)" : m.installed ? "Yes" : "No";
  const size = m && m.size_gb ? `${m.size_gb} GB` : "Unknown";
  const runs = llmRunsOn(data);
  const rows = [
    ["Model", escapeHtml(data.selected)],
    ["Size on disk", size],
    ["Installed", installed],
    ["Runs on", `<span class="llm-runs-on"></span>`],
    ["Memory", llmMemory(data, m)],
    ["Used for", "Cleanup, Polish, upload cleanup"],
  ];
  document.getElementById("llm-overview-grid").innerHTML = rows
    .map(([label, value]) => `<div class="overview-row"><span class="overview-label">${label}</span><span class="overview-value">${value}</span></div>`)
    .join("");
  setStatus(document.querySelector("#llm-overview-grid .llm-runs-on"), runs.warn ? "warn" : undefined, runs.text);
}

function llmOptionLabel(m) {
  let label = m.name;
  if (m.recommended_here) label += " — recommended for this PC";
  if (m.installed === false && m.size_gb) label += ` — not installed, ${m.size_gb} GB`;
  else if (m.installed === false) label += " — not installed";
  return label;
}

function renderLlmModels(data, rebuildOptions) {
  llmSavedModel = data.selected;
  lastLlmModels = data;
  refreshLlmHints();
  loadLlmStatus().catch(() => {}); // the hints follow the live status, fetched on its own
  llmSlowHint.hidden = !data.slow_hint;
  if (!data.reachable) {
    // Server down (or no model list): show the saved value only, nothing else can break.
    if (rebuildOptions) {
      llmModelSelect.replaceChildren(new Option(data.selected, data.selected));
      llmModelSelect.value = data.selected;
    }
    llmPullSelected.hidden = true;
    renderLlmOverview(data, null);
    return;
  }
  if (rebuildOptions) {
    llmModelSelect.replaceChildren();
    const rec = document.createElement("optgroup");
    rec.label = "Recommended";
    for (const m of data.recommended) {
      const opt = new Option(llmOptionLabel(m), m.name);
      if (!m.installed) opt.style.color = "var(--text-muted, gray)";
      rec.append(opt);
    }
    llmModelSelect.append(rec);
    if (data.other.length) {
      const other = document.createElement("optgroup");
      other.label = "Other installed models";
      for (const m of data.other) other.append(new Option(m.installed === false ? `${m.name} — not installed` : m.name, m.name));
      llmModelSelect.append(other);
    }
    llmModelSelect.value = data.selected;
  }
  const selectedModel = [...data.recommended, ...data.other].find((m) => m.name === data.selected) || null;
  renderLlmOverview(data, selectedModel);
  const selectedMissing = Boolean(selectedModel) && selectedModel.installed === false && data.protocol === "ollama";
  llmPullSelected.hidden = !selectedMissing || llmModelSelect.disabled;
  llmPullSelected.textContent = `Download ${data.selected}`;
  llmPullSelected.dataset.sizeGb = selectedModel && selectedModel.size_gb ? selectedModel.size_gb : "";
  llmModelSelect.dataset.notInstalled = JSON.stringify(
    Object.fromEntries(data.recommended.filter((m) => !m.installed).map((m) => [m.name, m.size_gb])),
  );
}

const llmRefresher = createLlmRefresher({
  fetchData: () => api("/api/settings/llm-models"),
  render: renderLlmModels,
  // On screen: the Settings tab is open, the page is visible and the dropdown is laid out.
  isActive: () =>
    !document.hidden && document.getElementById("tab-settings").classList.contains("active") && llmModelSelect.offsetParent !== null,
  schedule: (fn, ms) => setInterval(fn, ms),
  cancel: (t) => clearInterval(t),
});

// Draws the card now, whatever changed (after a save, a pull, or when the tab opens).
async function loadLlmModels() {
  await llmRefresher.refresh(true);
  llmRefresher.start();
}

document.addEventListener("visibilitychange", () => {
  // No polling while the page is hidden; one refresh as soon as it is visible again.
  if (document.hidden) {
    llmRefresher.stop();
  } else if (llmRefresher.started) {
    llmRefresher.start();
    llmRefresher.wake();
  }
});

async function saveLlmModel(name) {
  const resp = await postSetting("/api/settings/llm-model", { model: name });
  const s = await resp.json();
  if (s.error) alert(s.error);
  llmSavedModel = s.llm_model;
  await loadLlmModels();
  loadLlmStatus();
}

function showLlmPull(text, pct, busy) {
  llmPullBox.style.display = "flex";
  llmPullText.textContent = text;
  llmPullProgress.hidden = pct === null;
  if (pct !== null) llmPullProgress.value = pct;
  llmPullCancel.hidden = !busy;
}

function pollLlmPull() {
  clearInterval(llmPullTimer);
  llmPullTimer = setInterval(async () => {
    let p;
    try {
      p = await api("/api/settings/llm-pull");
    } catch {
      return;
    }
    if (p.status === "pulling") {
      const pct = p.total ? Math.round((p.completed / p.total) * 100) : null;
      showLlmPull(`Downloading ${p.model}: ${p.detail}${pct === null ? "" : ` ${pct}%`}`, pct, true);
      return;
    }
    clearInterval(llmPullTimer);
    llmModelSelect.disabled = false;
    if (p.status === "done") {
      showLlmPull(`${p.model} downloaded.`, null, false);
      await saveLlmModel(p.model); // selected only now that the pull succeeded
    } else if (p.status === "cancelled") {
      showLlmPull("Download cancelled.", null, false);
      await loadLlmModels();
    } else if (p.status === "error") {
      showLlmPull(`Download failed: ${p.error || "unknown error"}`, null, false);
      await loadLlmModels();
    }
    setTimeout(() => {
      if (!llmPullTimer || p.status !== "pulling") llmPullBox.style.display = "none";
    }, 5000);
  }, 1000);
}

// Asks, then downloads `name` through Ollama; the model is selected only after the pull succeeded.
async function startLlmPull(name, sizeGb) {
  const size = sizeGb ? ` (${sizeGb} GB)` : "";
  if (!confirm(`${name} is not installed. Download it now${size} through Ollama?`)) return;
  const resp = await postSetting("/api/settings/llm-pull", { model: name });
  const p = await resp.json();
  if (p.error && p.status !== "pulling") {
    alert(p.error);
    return;
  }
  llmModelSelect.disabled = true;
  llmPullSelected.hidden = true;
  showLlmPull(`Downloading ${name}...`, null, true);
  pollLlmPull();
}

llmModelSelect.addEventListener("change", async () => {
  const name = llmModelSelect.value;
  const missing = JSON.parse(llmModelSelect.dataset.notInstalled || "{}");
  if (!(name in missing)) {
    await saveLlmModel(name);
    return;
  }
  llmModelSelect.value = llmSavedModel; // the old model stays selected until the pull succeeded
  await startLlmPull(name, missing[name]);
});
// The saved model itself can be missing (a fresh Ollama): selecting it again fires no change, so it has a button.
llmPullSelected.addEventListener("click", () => startLlmPull(llmSavedModel, Number(llmPullSelected.dataset.sizeGb) || null));
llmPullCancel.addEventListener("click", () => fetch("/api/settings/llm-pull", { method: "DELETE" }));

// ── Model Switcher ──────────────────────────────────────────────────
const modelSelect = document.getElementById("model-select");

// " ✓ downloaded" or " · not downloaded (~670 MB)" for a model with a copy on this PC; nothing for the
// custom server, or when the cache could not be read.
function modelDownloadMark(m) {
  if (m.downloaded === true) return " ✓ downloaded";
  if (m.downloaded === false) return ` · not downloaded${m.size ? ` (${m.size})` : ""}`;
  return "";
}

async function loadModelSwitcher() {
  const [models, current] = await Promise.all([
    fetch("/api/models").then((r) => r.json()),
    fetch("/api/models/current").then((r) => r.json()),
  ]);
  modelSpecs = Object.fromEntries(models.map((m) => [m.id, m]));
  modelSelect.innerHTML = models.map((m) =>
    `<option value="${m.id}"${m.id === current.model ? " selected" : ""}>${m.name}${modelDownloadMark(m)}</option>`
  ).join("");
  renderModelOverview(current.model);
  // An in-process model (Parakeet on CPU) may still be downloading or loading from the start.
  const status = await fetch("/api/models/status").then((r) => r.json()).catch(() => null);
  if (status && (status.status === "downloading" || status.status === "loading")) {
    showModelStatus(STATUS_MESSAGES[status.status]);
    pollModelStatus(current.model);
  }
}

function renderModelOverview(modelId) {
  const m = modelSpecs[modelId];
  if (!m) return;
  const protocolLabel = m.protocol === "realtime_ws" ? "WebSocket (Realtime)" : "HTTP (OpenAI-compatible)";
  let runtimeLabel;
  if (m.custom) {
    runtimeLabel = "External server (no Docker)";
  } else if (m.inprocess_supported) {
    runtimeLabel = "CPU in-process · GPU via Docker";
  } else {
    runtimeLabel = "GPU (Docker)";
  }
  const rows = [
    ["Description", escapeHtml(m.description)],
    ["Architecture", escapeHtml(m.architecture)],
    ["Parameters", escapeHtml(m.parameters)],
    ["Protocol", protocolLabel],
    ["Runtime", runtimeLabel],
    ["Streaming", m.streaming ? "Yes" : "No"],
    ["Languages", escapeHtml(m.languages)],
    ["VRAM (approx.)", escapeHtml(m.vram_gb)],
    m.custom
      ? ["Server", escapeHtml(m.server_url || "Not set: add [inference.custom] url to config.toml")]
      : ["HuggingFace", `<span class="overview-hf-link" title="${escapeHtml(m.hf_name)}">${escapeHtml(m.hf_name)}</span>`],
  ];
  document.getElementById("model-overview-grid").innerHTML = rows.map(([label, value]) =>
    `<div class="overview-row"><span class="overview-label">${label}</span><span class="overview-value">${value}</span></div>`
  ).join("");
}

modelSelect.addEventListener("change", async () => {
  renderModelOverview(modelSelect.value);
  const prev = modelSelect.dataset.prev || modelSelect.value;
  modelSelect.disabled = true;
  showModelStatus("Initiating switch...");

  try {
    const res = await fetch("/api/models/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelSelect.value }),
    });
    const data = await res.json();
    if (!data.ok) {
      modelSelect.value = prev;
      hideModelStatus();
      modelSelect.disabled = false;
      return;
    }
    pollModelStatus(prev);
  } catch {
    modelSelect.value = prev;
    hideModelStatus();
    modelSelect.disabled = false;
  }
});

function showModelStatus(text) {
  const el = document.getElementById("model-switch-status");
  document.getElementById("model-status-text").textContent = text;
  el.style.display = "flex";
}

function hideModelStatus() {
  document.getElementById("model-switch-status").style.display = "none";
}

const STATUS_MESSAGES = {
  stopping: "Disconnecting current model...",
  starting: "Starting new container...",
  waiting: "Waiting for model to load...",
  downloading: "Downloading the model, first use only (about 670 MB)...",
  loading: "Loading the model...",
  ready: "Model ready!",
  error: "Switch failed.",
};

function pollModelStatus(prevModel) {
  const interval = setInterval(async () => {
    try {
      const data = await fetch("/api/models/status").then((r) => r.json());
      const msg = STATUS_MESSAGES[data.status] || data.status;
      showModelStatus(msg);

      if (data.status === "ready") {
        clearInterval(interval);
        modelSelect.dataset.prev = modelSelect.value;
        modelSelect.disabled = false;
        // The switch downloaded the model: redraw the list so its mark follows.
        loadModelSwitcher();
        setTimeout(hideModelStatus, 2000);
      } else if (data.status === "error") {
        clearInterval(interval);
        modelSelect.value = prevModel;
        modelSelect.disabled = false;
        showModelStatus("Error: " + (data.error || "Unknown"));
        setTimeout(hideModelStatus, 5000);
      } else if (data.status === "idle") {
        // Switch already completed or was no-op
        clearInterval(interval);
        modelSelect.disabled = false;
        hideModelStatus();
      }
    } catch {
      // Network error during poll, keep trying
    }
  }, 2000);
}
