/* NexusVox Dashboard — Tab switching, Chart.js rendering, settings */

// ── Globals ──────────────────────────────────────────────────────────
const COLORS = {
  accent: "#6c5ce7",
  accentAlpha: "rgba(108, 92, 231, 0.25)",
  green: "#00d2a0",
  greenAlpha: "rgba(0, 210, 160, 0.15)",
  orange: "#ffa94d",
  text: "#e4e6f0",
  dim: "#8b8fa8",
  grid: "#2e3348",
};

const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
  },
  scales: {
    x: { ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { color: COLORS.grid } },
    y: { ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { color: COLORS.grid }, beginAtZero: true },
  },
};

let charts = {};
let currentPeriod = "day";
let modelSpecs = {};

// ── API Helper ───────────────────────────────────────────────────────
function dateParams() {
  const start = document.getElementById("filter-start").value;
  const end = document.getElementById("filter-end").value;
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  return params;
}

async function api(path) {
  const dp = dateParams();
  const sep = path.includes("?") ? "&" : "?";
  const url = dp.toString() ? path + sep + dp.toString() : path;
  const res = await fetch(url);
  return res.json();
}

// ── Tab Switching ────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((s) => s.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");

    if (btn.dataset.tab === "analytics") loadAnalytics();
    if (btn.dataset.tab === "edit") loadFlaggedTranscriptions();
    if (btn.dataset.tab === "upload") loadFileTranscriptions();
    if (btn.dataset.tab === "review") loadReviewTranscriptions();
    if (btn.dataset.tab === "dev") loadBenchmarks();
  });
});

// ── Period Selector ──────────────────────────────────────────────────
document.querySelectorAll(".period-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".period-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentPeriod = btn.dataset.period;
    loadTimeline();
  });
});

document.getElementById("btn-refresh").addEventListener("click", loadAnalytics);
document.getElementById("btn-apply-dates").addEventListener("click", loadAnalytics);
document.getElementById("btn-clear-dates").addEventListener("click", () => {
  document.getElementById("filter-start").value = "";
  document.getElementById("filter-end").value = "";
  loadAnalytics();
});

// ── Settings ─────────────────────────────────────────────────────────
const toggle = document.getElementById("auto-lang-toggle");

toggle.addEventListener("change", async () => {
  await fetch("/api/settings/auto-language-detection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: toggle.checked }),
  });
});

async function loadSettings() {
  const s = await api("/api/settings");
  toggle.checked = s.auto_language_detection;
  await Promise.all([loadDeviceSettings(), loadModelSwitcher(), loadOsCommands(), loadVoiceCommands()]);
}

// ── Device (CPU/GPU) ─────────────────────────────────────────────────
const deviceSelect = document.getElementById("device-select");
const deviceResolved = document.getElementById("device-resolved");

async function loadDeviceSettings() {
  if (!deviceSelect) return;
  try {
    const d = await api("/api/device");
    deviceSelect.value = d.requested;
    renderDeviceHint(d);
  } catch {
    deviceResolved.textContent = "";
  }
}

function renderDeviceHint(d) {
  if (!deviceResolved) return;
  const resolvedLabel = d.resolved === "cuda" ? "GPU (CUDA)" : "CPU";
  const cudaNote = d.cuda_available ? "CUDA available on this system." : "No CUDA device detected.";
  deviceResolved.textContent = `Resolved: ${resolvedLabel} — ${cudaNote}`;
}

if (deviceSelect) {
  deviceSelect.addEventListener("change", async () => {
    try {
      const res = await fetch("/api/device", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: deviceSelect.value }),
      });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || "Failed to set device");
        await loadDeviceSettings();
        return;
      }
      renderDeviceHint(data);
      alert("Device preference saved. Restart NexusVox for the change to take effect.");
    } catch {
      await loadDeviceSettings();
    }
  });
}

// ── Nexus OS Commands ───────────────────────────────────────────────
const osCommandsToggle = document.getElementById("os-commands-toggle");

osCommandsToggle.addEventListener("change", async () => {
  await fetch("/api/os-commands/enabled", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: osCommandsToggle.checked }),
  });
});

async function loadOsCommands() {
  const data = await fetch("/api/os-commands").then((r) => r.json());
  osCommandsToggle.checked = data.enabled;

  // Render supported actions
  const actionsEl = document.getElementById("nexus-actions");
  actionsEl.innerHTML = data.supported_actions.map((a) =>
    `<div class="nexus-action"><code>${escapeHtml(a.syntax)}</code><span class="nexus-action-desc">${escapeHtml(a.description)}</span></div>`
  ).join("");

  // Render registered apps
  const appsEl = document.getElementById("nexus-apps");
  const appEntries = Object.entries(data.apps);
  if (!appEntries.length) {
    appsEl.innerHTML = '<p class="nexus-empty">No apps registered. Add apps in config.toml.</p>';
  } else {
    appsEl.innerHTML = appEntries.map(([name, path]) =>
      `<div class="nexus-app-tag"><span class="nexus-app-name">${escapeHtml(name)}</span><span class="nexus-app-path" title="${escapeHtml(path)}">${escapeHtml(path)}</span></div>`
    ).join("");
  }
}

// ── Voice Commands ──────────────────────────────────────────────────
const vcToggle = document.getElementById("voice-commands-toggle");
const vcNumbersToggle = document.getElementById("voice-numbers-toggle");
const vcBypassSymbolsToggle = document.getElementById("voice-bypass-symbols-toggle");

vcToggle.addEventListener("change", async () => {
  await fetch("/api/voice-commands/enabled", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: vcToggle.checked }),
  });
});

vcNumbersToggle.addEventListener("change", async () => {
  await fetch("/api/voice-commands/numbers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: vcNumbersToggle.checked }),
  });
});

vcBypassSymbolsToggle.addEventListener("change", async () => {
  await fetch("/api/voice-commands/bypass-symbols", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: vcBypassSymbolsToggle.checked }),
  });
});

async function loadVoiceCommands() {
  const data = await fetch("/api/voice-commands").then((r) => r.json());
  vcToggle.checked = data.enabled;
  vcNumbersToggle.checked = data.numbers_as_digits;
  vcBypassSymbolsToggle.checked = data.bypass_symbols;
  renderSymbolGrid(data.all_symbols, data.symbols);
}

function renderSymbolGrid(allSymbols, activeSymbols) {
  const activeSet = new Set(activeSymbols);
  const grid = document.getElementById("voice-symbols-grid");
  grid.innerHTML = allSymbols.map((s) => {
    const checked = activeSet.has(s.keyword) ? "checked" : "";
    const cls = s.safe ? "symbol-chip" : "symbol-chip ambiguous";
    return `<label class="${cls}">` +
      `<input type="checkbox" class="symbol-checkbox" data-keyword="${escapeHtml(s.keyword)}" ${checked}>` +
      `<code>${escapeHtml(s.keyword)}</code>` +
      `<span class="symbol-char">${escapeHtml(s.char)}</span>` +
      `</label>`;
  }).join("");

  grid.querySelectorAll(".symbol-checkbox").forEach((cb) => {
    cb.addEventListener("change", saveSymbols);
  });
}

async function saveSymbols() {
  const symbols = [...document.querySelectorAll(".symbol-checkbox:checked")]
    .map((cb) => cb.dataset.keyword);
  await fetch("/api/voice-commands/symbols", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
}

// ── Model Switcher ──────────────────────────────────────────────────
const modelSelect = document.getElementById("model-select");

async function loadModelSwitcher() {
  const [models, current] = await Promise.all([
    fetch("/api/models").then((r) => r.json()),
    fetch("/api/models/current").then((r) => r.json()),
  ]);
  modelSpecs = Object.fromEntries(models.map((m) => [m.id, m]));
  modelSelect.innerHTML = models.map((m) =>
    `<option value="${m.id}"${m.id === current.model ? " selected" : ""}>${m.name}</option>`
  ).join("");
  renderModelOverview(current.model);
}

function renderModelOverview(modelId) {
  const m = modelSpecs[modelId];
  if (!m) return;
  const protocolLabel = m.protocol === "realtime_ws" ? "WebSocket (Realtime)" : "HTTP (OpenAI-compatible)";
  let runtimeLabel;
  if (m.inprocess_supported && !m.requires_gpu) {
    runtimeLabel = "CPU in-process · GPU via Docker";
  } else if (m.requires_gpu) {
    runtimeLabel = "GPU only (Docker)";
  } else {
    runtimeLabel = "CPU in-process";
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
    ["HuggingFace", `<span class="overview-hf-link" title="${escapeHtml(m.hf_name)}">${escapeHtml(m.hf_name)}</span>`],
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

// ── Analytics Loading ────────────────────────────────────────────────
async function loadAnalytics() {
  const [overview, lang, hours, words, heatmap, confidence] = await Promise.all([
    api("/api/overview"),
    api("/api/language-distribution"),
    api("/api/peak-usage-hours"),
    api("/api/top-words?n=20"),
    api("/api/activity-heatmap"),
    api("/api/confidence-trend?period=" + currentPeriod),
  ]);

  renderOverview(overview);
  renderLanguage(lang);
  renderHours(hours);
  renderWords(words);
  renderHeatmap(heatmap);
  renderConfidence(confidence);
  loadTimeline();
}

async function loadTimeline() {
  const data = await api("/api/transcriptions-over-time?period=" + currentPeriod);
  renderTimeline(data);
}

// ── Render Functions ─────────────────────────────────────────────────
function renderOverview(d) {
  document.getElementById("stat-total").textContent = d.total_transcriptions;
  document.getElementById("stat-wpm").textContent = d.avg_wpm;
  document.getElementById("stat-min-wpm").textContent = d.min_wpm;
  document.getElementById("stat-max-wpm").textContent = d.max_wpm;
  document.getElementById("stat-duration").textContent = formatDuration(d.avg_duration_ms);
  document.getElementById("stat-confidence").textContent =
    d.avg_confidence != null ? (d.avg_confidence * 100).toFixed(1) + "%" : "--";
  document.getElementById("stat-saved").textContent = d.time_saved_minutes;
}

function formatDuration(ms) {
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(1) + "s";
}

function renderTimeline(d) {
  if (charts.timeline) charts.timeline.destroy();
  const ctx = document.getElementById("chart-timeline").getContext("2d");
  charts.timeline = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        borderColor: COLORS.accent,
        backgroundColor: COLORS.accentAlpha,
        fill: true,
        tension: 0.3,
        pointRadius: d.labels.length > 30 ? 0 : 3,
        pointBackgroundColor: COLORS.accent,
      }],
    },
    options: {
      ...chartDefaults,
      plugins: { ...chartDefaults.plugins, tooltip: { mode: "index", intersect: false } },
    },
  });
}

function renderLanguage(d) {
  if (charts.language) charts.language.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-language").getContext("2d");
  charts.language = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        backgroundColor: [COLORS.accent, COLORS.green, COLORS.orange, "#ff6b6b", "#74b9ff"],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: "bottom", labels: { color: COLORS.dim, padding: 16, font: { size: 12 } } },
      },
    },
  });
}

function renderHours(d) {
  if (charts.hours) charts.hours.destroy();
  const ctx = document.getElementById("chart-hours").getContext("2d");
  charts.hours = new Chart(ctx, {
    type: "bar",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        backgroundColor: d.values.map((v) => {
          const max = Math.max(...d.values);
          return v === max ? COLORS.accent : COLORS.accentAlpha;
        }),
        borderRadius: 4,
      }],
    },
    options: chartDefaults,
  });
}

function renderWords(d) {
  if (charts.words) charts.words.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-words").getContext("2d");
  charts.words = new Chart(ctx, {
    type: "bar",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        backgroundColor: COLORS.green,
        borderRadius: 4,
      }],
    },
    options: {
      ...chartDefaults,
      indexAxis: "y",
      scales: {
        x: { ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { color: COLORS.grid }, beginAtZero: true },
        y: { ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

function renderHeatmap(d) {
  const container = document.getElementById("heatmap-container");

  // Show only every 2nd hour label to save space.
  const hourLabels = d.hours.map((h) => (h % 2 === 0 ? String(h).padStart(2, "0") : ""));

  const maxVal = Math.max(...d.grid.flat(), 1);

  let html = '<table class="heatmap-table"><thead><tr><th></th>';
  hourLabels.forEach((h) => { html += "<th>" + h + "</th>"; });
  html += "</tr></thead><tbody>";

  d.days.forEach((day, di) => {
    html += '<tr><td class="day-label">' + day + "</td>";
    d.grid[di].forEach((val) => {
      const intensity = val / maxVal;
      const bg = intensity === 0
        ? "rgba(108, 92, 231, 0.05)"
        : `rgba(108, 92, 231, ${0.15 + intensity * 0.75})`;
      html += '<td style="background:' + bg + '">' + (val || "") + "</td>";
    });
    html += "</tr>";
  });

  html += "</tbody></table>";
  container.innerHTML = html;
}

// ── Confidence Trend Chart ───────────────────────────────────────────
function renderConfidence(d) {
  if (charts.confidence) charts.confidence.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-confidence").getContext("2d");
  charts.confidence = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values.map((v) => +(v * 100).toFixed(1)),
        borderColor: COLORS.green,
        backgroundColor: COLORS.greenAlpha,
        fill: true,
        tension: 0.3,
        pointRadius: d.labels.length > 30 ? 0 : 3,
        pointBackgroundColor: COLORS.green,
      }],
    },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, min: 0, max: 100, ticks: { ...chartDefaults.scales.y.ticks, callback: (v) => v + "%" } },
      },
      plugins: { ...chartDefaults.plugins, tooltip: { mode: "index", intersect: false, callbacks: { label: (c) => c.parsed.y + "%" } } },
    },
  });
}

// ── Flagged Transcriptions (Edit Tab) ────────────────────────────────
async function loadFlaggedTranscriptions() {
  const data = await fetch("/api/flagged").then((r) => r.json());
  const container = document.getElementById("flagged-list");

  if (!data.length) {
    container.innerHTML = '<p class="empty-state">No flagged transcriptions yet. Say "nexus flag" after a recording to flag it.</p>';
    return;
  }

  container.innerHTML = data.map((item) => `
    <div class="flagged-card" data-id="${item.id}">
      <div class="flagged-meta">
        <span>${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span>
        <span class="flagged-lang">${(item.language || "").toUpperCase()}</span>
        ${item.confidence != null ? `<span class="flagged-conf">${(item.confidence * 100).toFixed(1)}%</span>` : ""}
      </div>
      <div class="flagged-original"><strong>Original:</strong> ${escapeHtml(item.text)}</div>
      ${item.audio_path ? `<button class="flagged-play-btn" onclick="playAudio(${item.id}, this)">&#9654; Play</button>` : ""}
      <textarea class="flagged-textarea" rows="2" placeholder="Enter correct text...">${escapeHtml(item.corrected_text || "")}</textarea>
      <button class="flagged-save-btn" onclick="saveCorrection(${item.id}, this)">Save</button>
    </div>
  `).join("");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function playAudio(tid, btn) {
  const audio = new Audio(`/api/audio/${tid}`);
  btn.disabled = true;
  btn.textContent = "Playing...";
  audio.onended = () => { btn.textContent = "\u25B6 Play"; btn.disabled = false; };
  audio.onerror = () => { btn.textContent = "\u25B6 Play"; btn.disabled = false; };
  audio.play();
}

async function saveCorrection(id, btn) {
  const card = btn.closest(".flagged-card");
  const text = card.querySelector(".flagged-textarea").value;
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    await fetch(`/api/flagged/${id}/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corrected_text: text }),
    });
    btn.textContent = "Saved";
    setTimeout(() => { btn.textContent = "Save"; btn.disabled = false; }, 1500);
  } catch {
    btn.textContent = "Error";
    btn.disabled = false;
  }
}

// ── Review Transcriptions (Review Tab) ───────────────────────────────
async function loadReviewTranscriptions() {
  const data = await fetch("/api/review").then((r) => r.json());
  const container = document.getElementById("review-list");

  if (!data.length) {
    container.innerHTML = '<p class="empty-state">No unreviewed recordings. All caught up!</p>';
    return;
  }

  container.innerHTML = data.map((item) => {
    const isFlagged = item.flagged === 1;
    const defaultCorrect = !isFlagged;
    return `
    <div class="flagged-card${isFlagged ? " review-card-flagged" : ""}" data-id="${item.id}">
      <div class="flagged-meta">
        <span>${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span>
        <span class="flagged-lang">${(item.language || "").toUpperCase()}</span>
        ${item.confidence != null ? `<span class="flagged-conf">${(item.confidence * 100).toFixed(1)}%</span>` : ""}
        <span class="review-duration">${formatDuration(item.duration_ms)}</span>
      </div>
      <div class="flagged-original"><strong>Original:</strong> ${escapeHtml(item.text)}</div>
      <button class="flagged-play-btn" onclick="playAudio(${item.id}, this)">&#9654; Play</button>
      <div class="review-checkbox-row">
        <label class="review-label">
          <input type="checkbox" class="review-correct-cb" ${defaultCorrect ? "checked" : ""}
            onchange="toggleReviewTextarea(this)">
          Transcription is correct
        </label>
      </div>
      <textarea class="flagged-textarea review-correction" rows="2"
        placeholder="Enter correct text..." style="display: ${defaultCorrect ? "none" : "block"}">${escapeHtml(item.corrected_text || "")}</textarea>
      <button class="flagged-save-btn" onclick="submitReview(${item.id}, this)">Submit Review</button>
    </div>`;
  }).join("");
}

function toggleReviewTextarea(cb) {
  const card = cb.closest(".flagged-card");
  card.querySelector(".review-correction").style.display = cb.checked ? "none" : "block";
}

async function submitReview(id, btn) {
  const card = btn.closest(".flagged-card");
  const isCorrect = card.querySelector(".review-correct-cb").checked;
  const correctedText = card.querySelector(".review-correction").value || null;

  btn.disabled = true;
  btn.textContent = "Submitting...";
  try {
    const res = await fetch(`/api/review/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_correct: isCorrect, corrected_text: isCorrect ? null : correctedText }),
    });
    const data = await res.json();
    if (data.ok) {
      card.style.transition = "opacity 0.3s";
      card.style.opacity = "0";
      setTimeout(() => {
        card.remove();
        if (!document.getElementById("review-list").children.length) {
          document.getElementById("review-list").innerHTML = '<p class="empty-state">No unreviewed recordings. All caught up!</p>';
        }
      }, 300);
    } else {
      btn.textContent = "Error";
      btn.disabled = false;
    }
  } catch {
    btn.textContent = "Error";
    btn.disabled = false;
  }
}

// ── File Upload (Upload Tab) ─────────────────────────────────────────
const uploadZone = document.getElementById("upload-zone");
const uploadInput = document.getElementById("upload-input");
const uploadBrowse = document.getElementById("upload-browse");

uploadBrowse.addEventListener("click", (e) => {
  e.preventDefault();
  uploadInput.click();
});

uploadInput.addEventListener("change", () => {
  if (uploadInput.files.length) handleFileUpload(uploadInput.files[0]);
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
  if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0]);
});

async function handleFileUpload(file) {
  const statusEl = document.getElementById("upload-status");
  const statusText = document.getElementById("upload-status-text");
  const errorEl = document.getElementById("upload-error");

  errorEl.style.display = "none";
  statusEl.style.display = "flex";
  statusText.textContent = "Transcribing " + file.name + "...";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/file-transcribe", { method: "POST", body: formData });
    const data = await res.json();

    statusEl.style.display = "none";
    if (!data.ok) {
      errorEl.textContent = data.error || "Upload failed";
      errorEl.style.display = "block";
      return;
    }
    // Reset file input
    uploadInput.value = "";
    loadFileTranscriptions();
  } catch (err) {
    statusEl.style.display = "none";
    errorEl.textContent = "Network error: " + err.message;
    errorEl.style.display = "block";
  }
}

async function loadFileTranscriptions() {
  const data = await fetch("/api/file-transcriptions").then((r) => r.json());
  const container = document.getElementById("upload-list");

  if (!data.length) {
    container.innerHTML = '<p class="empty-state">No file transcriptions yet.</p>';
    return;
  }

  container.innerHTML = data.map((item) => `
    <div class="flagged-card">
      <div class="flagged-meta">
        <span>${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span>
        <span class="flagged-lang">${(item.language || "").toUpperCase()}</span>
        <span class="upload-model">${escapeHtml(item.model || "")}</span>
        <span class="upload-duration">${formatDuration(item.duration_ms)}</span>
      </div>
      <div class="upload-filename">${escapeHtml(item.original_filename)}</div>
      <div class="flagged-original">${escapeHtml(item.text)}</div>
    </div>
  `).join("");
}

// ── Dev Mode Detection ───────────────────────────────────────────────
const DEV_MODE = new URLSearchParams(window.location.search).has("dev");

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  if (DEV_MODE) {
    document.querySelectorAll("[data-dev-only]").forEach((el) => (el.style.display = ""));
  }
});

// ── Benchmarks (Dev Tab) ─────────────────────────────────────────────

let benchmarkCache = null;
let currentBenchmarkDetail = null;

async function loadBenchmarks() {
  if (!DEV_MODE) return;
  try {
    const comparison = await fetch("/api/benchmarks/compare").then((r) => r.json());
    benchmarkCache = comparison;
    populateBenchmarkSelector(comparison.benchmarks);
    showComparison(comparison);
  } catch (err) {
    console.error("Failed to load benchmarks:", err);
  }
}

function populateBenchmarkSelector(benchmarks) {
  const sel = document.getElementById("dev-benchmark-select");
  // Keep the first default option, remove the rest
  while (sel.options.length > 1) sel.remove(1);
  benchmarks.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b.filename;
    opt.textContent = `${b.model_key} — ${b.lang.replace("_", " ").toUpperCase()} (WER ${(b.wer_mean * 100).toFixed(1)}%)`;
    sel.appendChild(opt);
  });
}

// Wire up selector & compare-all button
document.getElementById("dev-benchmark-select").addEventListener("change", async (e) => {
  if (!e.target.value) {
    showComparison(benchmarkCache);
    return;
  }
  await loadBenchmarkDetail(e.target.value);
});

document.getElementById("dev-view-compare").addEventListener("click", () => {
  document.getElementById("dev-benchmark-select").value = "";
  showComparison(benchmarkCache);
});

// ── Comparison View ──────────────────────────────────────────────────

function showComparison(comparison) {
  if (!comparison || !comparison.benchmarks.length) {
    document.getElementById("dev-empty").style.display = "";
    document.getElementById("dev-content").style.display = "none";
    return;
  }
  document.getElementById("dev-empty").style.display = "none";
  document.getElementById("dev-content").style.display = "";
  document.getElementById("dev-comparison").style.display = "";
  document.getElementById("dev-detail").style.display = "none";

  renderComparisonStats(comparison.benchmarks);
  renderComparisonTable(comparison);
  renderWerChart(comparison);
  renderRtfChart(comparison);
}

function renderComparisonStats(benchmarks) {
  const totalSamples = benchmarks.reduce((s, b) => s + b.total_samples, 0);
  const totalAudio = benchmarks.reduce((s, b) => s + b.total_audio_duration_s, 0);
  const bestWer = Math.min(...benchmarks.map((b) => b.wer_mean));
  const bestRtf = Math.min(...benchmarks.map((b) => b.rtf));

  document.getElementById("dev-stats-row").innerHTML = `
    <div class="stat-card"><span class="stat-value">${benchmarks.length}</span><span class="stat-label">Benchmarks</span></div>
    <div class="stat-card"><span class="stat-value">${totalSamples.toLocaleString()}</span><span class="stat-label">Total Samples</span></div>
    <div class="stat-card"><span class="stat-value">${(totalAudio / 60).toFixed(0)} min</span><span class="stat-label">Total Audio</span></div>
    <div class="stat-card"><span class="stat-value">${(bestWer * 100).toFixed(1)}%</span><span class="stat-label">Best WER</span></div>
    <div class="stat-card"><span class="stat-value">${bestRtf.toFixed(4)}</span><span class="stat-label">Best RTF</span></div>
  `;
}

function renderComparisonTable(comparison) {
  const container = document.getElementById("dev-comparison-table-container");
  const rows = comparison.benchmarks
    .map((b) => {
      const badge = b.passes_target
        ? `<span class="pass-badge">Pass</span>`
        : `<span class="fail-badge">Fail</span>`;
      const targetPct = b.wer_target != null ? (b.wer_target * 100).toFixed(0) + "%" : "—";
      return `<tr>
        <td>${escapeHtml(b.model_key)}</td>
        <td>${b.lang.replace("_", " ").toUpperCase()}</td>
        <td class="num">${(b.wer_mean * 100).toFixed(2)}%</td>
        <td class="num">${(b.wer_median * 100).toFixed(2)}%</td>
        <td class="num">${(b.wer_p90 * 100).toFixed(2)}%</td>
        <td class="num">${(b.wer_stddev * 100).toFixed(2)}%</td>
        <td class="num">${targetPct}</td>
        <td>${badge}</td>
        <td class="num">${b.rtf.toFixed(4)}</td>
        <td class="num">${b.total_samples}</td>
        <td>${b.timestamp ? new Date(b.timestamp).toLocaleDateString() : "—"}</td>
      </tr>`;
    })
    .join("");

  container.innerHTML = `
    <table class="benchmark-table">
      <thead><tr>
        <th>Model</th><th>Language</th><th>WER Mean</th><th>WER Median</th><th>WER P90</th><th>WER Std</th><th>Target</th><th>Status</th><th>RTF</th><th>Samples</th><th>Date</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderWerChart(comparison) {
  const ctx = document.getElementById("chart-dev-wer");
  if (charts.devWer) charts.devWer.destroy();

  const labels = comparison.benchmarks.map((b) => `${b.model_key}\n${b.lang.replace("_", " ").toUpperCase()}`);
  const werData = comparison.benchmarks.map((b) => +(b.wer_mean * 100).toFixed(2));
  const bgColors = comparison.benchmarks.map((b) => (b.passes_target ? COLORS.green : COLORS.orange));

  // Build annotation lines for targets
  const annotations = {};
  const targetEntries = Object.entries(comparison.targets);
  const targetColors = [COLORS.accent, COLORS.orange];
  targetEntries.forEach(([lang, val], i) => {
    annotations["target_" + lang] = {
      type: "line",
      yMin: val * 100,
      yMax: val * 100,
      borderColor: targetColors[i] || COLORS.dim,
      borderDash: [6, 4],
      borderWidth: 2,
      label: {
        display: true,
        content: `Target ${lang.replace("_", " ").toUpperCase()} (${(val * 100).toFixed(0)}%)`,
        position: "end",
        color: COLORS.text,
        font: { size: 10 },
        backgroundColor: "rgba(0,0,0,0.5)",
      },
    };
  });

  charts.devWer = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ data: werData, backgroundColor: bgColors, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      plugins: {
        legend: { display: false },
        annotation: { annotations },
      },
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: { display: true, text: "WER %", color: COLORS.dim } },
      },
    },
  });
}

function renderRtfChart(comparison) {
  const ctx = document.getElementById("chart-dev-rtf");
  if (charts.devRtf) charts.devRtf.destroy();

  const labels = comparison.benchmarks.map((b) => `${b.model_key}\n${b.lang.replace("_", " ").toUpperCase()}`);
  const rtfData = comparison.benchmarks.map((b) => +b.rtf.toFixed(4));

  charts.devRtf = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ data: rtfData, backgroundColor: COLORS.accent, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      indexAxis: "y",
      scales: {
        x: { ...chartDefaults.scales.x, title: { display: true, text: "RTF (lower = faster)", color: COLORS.dim } },
        y: { ...chartDefaults.scales.y, ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

// ── Detail View (single benchmark) ───────────────────────────────────

async function loadBenchmarkDetail(filename) {
  try {
    const data = await fetch(`/api/benchmarks/${encodeURIComponent(filename)}`).then((r) => r.json());
    if (data.error) {
      console.error(data.error);
      return;
    }
    currentBenchmarkDetail = data;
    showDetail(data);
  } catch (err) {
    console.error("Failed to load benchmark detail:", err);
  }
}

function showDetail(data) {
  document.getElementById("dev-comparison").style.display = "none";
  document.getElementById("dev-detail").style.display = "";

  const s = data.summary;
  document.getElementById("dev-detail-stats").innerHTML = `
    <div class="stat-card"><span class="stat-value">${escapeHtml(s.model_key)}</span><span class="stat-label">Model</span></div>
    <div class="stat-card"><span class="stat-value">${s.lang.replace("_", " ").toUpperCase()}</span><span class="stat-label">Language</span></div>
    <div class="stat-card"><span class="stat-value">${(s.wer_mean * 100).toFixed(2)}%</span><span class="stat-label">WER Mean</span></div>
    <div class="stat-card"><span class="stat-value">${(s.wer_median * 100).toFixed(2)}%</span><span class="stat-label">WER Median</span></div>
    <div class="stat-card"><span class="stat-value">${(s.wer_p90 * 100).toFixed(2)}%</span><span class="stat-label">WER P90</span></div>
    <div class="stat-card"><span class="stat-value">${s.rtf.toFixed(4)}</span><span class="stat-label">RTF</span></div>
    <div class="stat-card"><span class="stat-value">${s.total_samples}</span><span class="stat-label">Samples</span></div>
    <div class="stat-card"><span class="stat-value">${s.failed_samples}</span><span class="stat-label">Failed</span></div>
  `;

  renderWerDistribution(data.samples);
  renderLatencyScatter(data.samples);
  renderSampleExplorer(data.samples);
}

function renderWerDistribution(samples) {
  const ctx = document.getElementById("chart-dev-wer-dist");
  if (charts.devWerDist) charts.devWerDist.destroy();

  // Bucket samples by WER %
  const buckets = [
    { label: "0%", min: 0, max: 0.001 },
    { label: "0-5%", min: 0.001, max: 0.05 },
    { label: "5-10%", min: 0.05, max: 0.10 },
    { label: "10-20%", min: 0.10, max: 0.20 },
    { label: "20-50%", min: 0.20, max: 0.50 },
    { label: "50%+", min: 0.50, max: Infinity },
  ];

  const counts = buckets.map((b) => samples.filter((s) => s.wer >= b.min && s.wer < b.max).length);
  const colors = [COLORS.green, COLORS.green, COLORS.accent, COLORS.orange, COLORS.orange, "#ff6b6b"];

  charts.devWerDist = new Chart(ctx, {
    type: "bar",
    data: {
      labels: buckets.map((b) => b.label),
      datasets: [{ data: counts, backgroundColor: colors, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        x: { ...chartDefaults.scales.x, title: { display: true, text: "WER Range", color: COLORS.dim } },
        y: { ...chartDefaults.scales.y, title: { display: true, text: "Samples", color: COLORS.dim } },
      },
    },
  });
}

function renderLatencyScatter(samples) {
  const ctx = document.getElementById("chart-dev-latency");
  if (charts.devLatency) charts.devLatency.destroy();

  const points = samples
    .filter((s) => s.latency_s != null && s.audio_duration_s != null)
    .map((s) => ({ x: s.audio_duration_s, y: s.latency_s }));

  charts.devLatency = new Chart(ctx, {
    type: "scatter",
    data: {
      datasets: [
        {
          data: points,
          backgroundColor: COLORS.accent + "88",
          borderColor: COLORS.accent,
          pointRadius: 3,
          pointHoverRadius: 5,
        },
      ],
    },
    options: {
      ...chartDefaults,
      scales: {
        x: {
          ...chartDefaults.scales.x,
          title: { display: true, text: "Audio Duration (s)", color: COLORS.dim },
          beginAtZero: true,
        },
        y: {
          ...chartDefaults.scales.y,
          title: { display: true, text: "Latency (s)", color: COLORS.dim },
        },
      },
    },
  });
}

// ── Sample Explorer ──────────────────────────────────────────────────

function renderSampleExplorer(samples) {
  const container = document.getElementById("dev-sample-list");
  const countEl = document.getElementById("dev-sample-count");
  const werFilter = document.getElementById("dev-wer-filter");
  const searchFilter = document.getElementById("dev-sample-search");

  function render() {
    const maxWer = parseFloat(werFilter.value) / 100 || 1;
    const query = (searchFilter.value || "").toLowerCase();

    const filtered = samples.filter((s) => {
      if (s.wer > maxWer) return false;
      if (query && !s.reference.toLowerCase().includes(query) && !s.hypothesis.toLowerCase().includes(query)) return false;
      return true;
    });

    // Sort by WER descending (worst first)
    filtered.sort((a, b) => b.wer - a.wer);

    countEl.textContent = `${filtered.length} / ${samples.length} samples`;

    // Limit rendering to 100 samples for performance
    const shown = filtered.slice(0, 100);

    container.innerHTML = shown
      .map((s) => {
        const werPct = (s.wer * 100).toFixed(1);
        const werClass = s.wer <= 0.05 ? "good" : s.wer <= 0.15 ? "mid" : "bad";
        const diffHtml = wordDiff(s.reference, s.hypothesis);
        return `
        <div class="sample-card">
          <div class="sample-card-header">
            <span class="sample-wer ${werClass}">${werPct}% WER</span>
            <span>#${s.index}</span>
            <span>ID: ${escapeHtml(String(s.id))}</span>
            <span>${s.audio_duration_s.toFixed(1)}s audio</span>
            <span>${s.latency_s != null ? s.latency_s.toFixed(3) + "s latency" : ""}</span>
          </div>
          <div class="sample-text"><span class="label">Ref</span>${escapeHtml(s.reference)}</div>
          <div class="sample-text"><span class="label">Hyp</span>${diffHtml}</div>
        </div>`;
      })
      .join("");

    if (filtered.length > 100) {
      container.innerHTML += `<div class="empty-state"><p>Showing first 100 of ${filtered.length} samples. Narrow the filter to see more.</p></div>`;
    }
  }

  werFilter.addEventListener("input", render);
  searchFilter.addEventListener("input", render);
  render();
}

/**
 * Simple word-level diff: marks added/deleted words between reference and hypothesis.
 */
function wordDiff(ref, hyp) {
  const refWords = ref.split(/\s+/);
  const hypWords = hyp.split(/\s+/);

  // LCS-based diff for reasonable accuracy
  const m = refWords.length;
  const n = hypWords.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = refWords[i - 1] === hypWords[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Backtrack
  const parts = [];
  let i = m,
    j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && refWords[i - 1] === hypWords[j - 1]) {
      parts.push(escapeHtml(hypWords[j - 1]));
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      parts.push(`<span class="diff-add">${escapeHtml(hypWords[j - 1])}</span>`);
      j--;
    } else {
      parts.push(`<span class="diff-del">${escapeHtml(refWords[i - 1])}</span>`);
      i--;
    }
  }

  return parts.reverse().join(" ");
}
