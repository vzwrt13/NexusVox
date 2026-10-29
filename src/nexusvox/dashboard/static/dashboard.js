/* NexusVox Dashboard — Tab switching, Chart.js rendering, settings */

// ── Globals ──────────────────────────────────────────────────────────
// Chart colors come from the :root variables in style.css, so the stylesheet is the single source
// of the theme. The fallbacks only matter when the stylesheet failed to load.
const rootStyle = getComputedStyle(document.documentElement);
function cssVar(name, fallback) {
  return rootStyle.getPropertyValue(name).trim() || fallback;
}

const COLORS = {
  accent: cssVar("--accent", "#6c5ce7"),
  accentLight: cssVar("--accent-light", "#a29bfe"),
  accentSoft: cssVar("--accent-soft", "rgba(162, 155, 254, 0.35)"),
  accentFill: cssVar("--accent-fill", "rgba(108, 92, 231, 0.2)"),
  green: cssVar("--green", "#00d2a0"),
  greenFill: cssVar("--green-fill", "rgba(0, 210, 160, 0.15)"),
  orange: cssVar("--orange", "#ffa94d"),
  red: cssVar("--red", "#ff6b6b"),
  text: cssVar("--text", "#f5f5f7"),
  dim: cssVar("--text-dim", "#a1a1aa"),
  grid: cssVar("--border", "rgba(255, 255, 255, 0.08)"),
};

// "#rrggbb" plus an alpha -> rgba(), for colors that vary per data point (the heatmap).
function withAlpha(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// Chart.js is a global from chart.min.js; without it only the charts break, not the rest of the page.
if (window.Chart) {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = COLORS.dim;
}

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
// The active tab is mirrored into the URL hash (e.g. "#analytics") so a page
// refresh, bookmark, or back/forward navigation lands on the same tab.
function activateTab(name) {
  const btn = document.querySelector(`.tab[data-tab="${name}"]`);
  const panel = document.getElementById("tab-" + name);
  if (!btn || !panel) return false;

  document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach((s) => s.classList.remove("active"));
  btn.classList.add("active");
  panel.classList.add("active");

  if (name === "analytics") loadAnalytics();
  if (name === "edit") loadFlaggedTranscriptions();
  if (name === "meetings") openMeetingsTab();
  if (name === "settings") loadMeetingSettings();
  if (name === "upload") {
    loadFileTranscriptions();
    refreshDiarizeToggle();
    resumeUploadJob();
  }
  if (name === "review") loadReviewTranscriptions();
  if (name === "dev") loadBenchmarks();
  if (name.startsWith("ext-")) openExtensionPanel(name.slice(4));
  return true;
}

function bindTab(btn) {
  btn.addEventListener("click", () => {
    const name = btn.dataset.tab;
    if (window.location.hash !== "#" + name) {
      history.replaceState(null, "", "#" + name);
    }
    activateTab(name);
  });
}

document.querySelectorAll(".tab").forEach(bindTab);

// ── Extensions ───────────────────────────────────────────────────────
// An installed extension package may add tabs and export links (docs/EXTENSIONS.md). With none
// installed GET /api/extensions lists nothing and the page is unchanged.
const extensionPanels = {};
let extensionExports = [];
let extensionUploadActions = [];
const uploadActionHandlers = {};

// Extension scripts call NexusVoxExt.registerPanel(id, container => ...) to draw into their tab.
window.NexusVoxExt = {
  registerPanel(id, render) {
    const panel = extensionPanels[id];
    if (!panel) return;
    panel.render = render;
    if (panel.container) renderExtensionPanel(panel);
  },
  // A panel script registers what its upload action does: handler(upload, button).
  registerUploadAction(id, handler) {
    uploadActionHandlers[id] = handler;
  },
};

function renderExtensionPanel(panel) {
  try {
    panel.render(panel.container);
  } catch (err) {
    console.error("Extension tab failed:", panel.title, err);
    panel.container.textContent = "This tab failed to load.";
  }
}

async function loadExtensions() {
  try {
    const info = await fetch("/api/extensions").then((r) => r.json());
    extensionExports = info.exports || [];
    extensionUploadActions = info.upload_actions || [];
    const nav = document.querySelector("nav.tabs");
    const main = document.querySelector("main");
    (info.panels || []).forEach((p) => {
      const btn = document.createElement("button");
      btn.className = "tab";
      btn.dataset.tab = "ext-" + p.id;
      btn.textContent = p.title;
      nav.appendChild(btn);
      bindTab(btn);
      const section = document.createElement("section");
      section.id = "tab-ext-" + p.id;
      section.className = "tab-content";
      main.appendChild(section);
      extensionPanels[p.id] = { title: p.title, script: p.script, section, container: null, render: null };
    });
  } catch (err) {
    console.error("Failed to load extensions:", err);
  }
  // The Upload history may have been drawn before the export targets were known.
  if ((extensionExports.length || extensionUploadActions.length) && document.getElementById("tab-upload").classList.contains("active")) {
    loadFileTranscriptions();
  }
}

// The panel's script is fetched when its tab is first opened and renders into the tab once.
function openExtensionPanel(id) {
  const panel = extensionPanels[id];
  if (!panel || panel.requested) return;
  panel.requested = true;
  panel.container = document.createElement("div");
  panel.section.appendChild(panel.container);
  const script = document.createElement("script");
  script.src = panel.script;
  // Resolves with true once the script ran, false when it could not be loaded.
  panel.ready = new Promise((resolve) => {
    script.onload = () => resolve(true);
    script.onerror = () => {
      panel.container.textContent = "This tab failed to load.";
      resolve(false);
    };
  });
  document.head.appendChild(script);
}

function restoreTabFromHash() {
  const name = window.location.hash.replace(/^#/, "");
  if (!name || (name === "dev" && !DEV_MODE)) return;
  activateTab(name);
}

window.addEventListener("hashchange", restoreTabFromHash);

// ── Period Selector ──────────────────────────────────────────────────
document.querySelectorAll(".period-btn[data-period]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".period-btn[data-period]").forEach((b) => b.classList.remove("active"));
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
const translateToggle = document.getElementById("translate-toggle");
const languageSelect = document.getElementById("language-select");
const hotkeyModeSelect = document.getElementById("hotkey-mode-select");
const micGuardToggle = document.getElementById("mic-guard-toggle");
const storeAudioToggle = document.getElementById("store-audio-toggle");
const logFullTextToggle = document.getElementById("log-full-text-toggle");
const openLogFolderButton = document.getElementById("open-log-folder");
const logOpenError = document.getElementById("log-open-error");
const logDirLabel = document.getElementById("log-dir");
const learnFromEditsToggle = document.getElementById("learn-from-edits-toggle");
const hotkeyModeHint = document.getElementById("hotkey-mode-hint");
const hotkeyMods = Array.from(document.querySelectorAll("#hotkey-mods input"));
const hotkeyKeySelect = document.getElementById("hotkey-key-select");
const pasteKeySelect = document.getElementById("paste-key-select");
const polishKeySelect = document.getElementById("polish-key-select");
const polishTerminalsToggle = document.getElementById("polish-terminals-toggle");
const llmModelInput = document.getElementById("llm-model-input");
const meetingOwnLabelInput = document.getElementById("meeting-own-label-input");
let meetingOwnLabelSaved = "";
const feedbackModeSelect = document.getElementById("feedback-mode-select");
const cleanupLevelSelect = document.getElementById("cleanup-level-select");
const llmStatus = document.getElementById("llm-status");

async function loadLlmStatus() {
  const s = await api("/api/settings/llm-status");
  llmStatus.className = "status-line " + (s.reachable ? "ok" : "down");
  llmStatus.textContent = s.reachable
    ? `● Model server reachable at ${s.url} (model ${s.model})`
    : `○ No model server at ${s.url} — polish plays the error beep`;
}
const hotkeyRecordBtn = document.getElementById("hotkey-record-btn");
const hotkeyStatus = document.getElementById("hotkey-status");
const hotkeyResetBtn = document.getElementById("hotkey-reset-btn");
const hotkeyKeycaps = document.getElementById("hotkey-keycaps");
const hotkeyBadge = document.getElementById("hotkey-badge");
const hotkeyDefaultName = document.getElementById("hotkey-default-name");
let hotkeyDefault = { modifiers: ["ctrl", "shift", "alt"], key: "" };

function describeHotkey(modifiers, key) {
  const parts = modifiers.map((m) => m.charAt(0).toUpperCase() + m.slice(1));
  if (key) parts.push(key.length > 1 ? key.replace("_", " ") : key);
  return parts.join("+");
}

function renderHotkeyHint(mode, modifiers, key) {
  const keys = describeHotkey(modifiers, key);
  hotkeyModeHint.textContent =
    mode === "toggle"
      ? "Press " + keys + " once to start recording, press it again to stop."
      : "Hold " + keys + " to record, release to stop.";
}

function renderKeycaps(modifiers, key) {
  hotkeyKeycaps.replaceChildren();
  describeHotkey(modifiers, key).split("+").forEach((name, i) => {
    if (i) hotkeyKeycaps.append(Object.assign(document.createElement("span"), { className: "keycaps-plus", textContent: "+" }));
    hotkeyKeycaps.append(Object.assign(document.createElement("kbd"), { textContent: name }));
  });
}

function isDefaultHotkey(modifiers, key) {
  return key === hotkeyDefault.key && modifiers.join("+") === hotkeyDefault.modifiers.join("+");
}

function renderHotkey(s) {
  if (s.hotkey_default) hotkeyDefault = s.hotkey_default;
  hotkeyDefaultName.textContent = describeHotkey(hotkeyDefault.modifiers, hotkeyDefault.key);
  const key = s.hotkey_key || "";
  if (!hotkeyKeySelect.options.length) {
    hotkeyKeySelect.append(new Option("No key (modifiers only)", ""));
    for (const k of s.hotkey_key_options || []) hotkeyKeySelect.append(new Option(k.replace("_", " "), k));
  }
  for (const cb of hotkeyMods) cb.checked = s.hotkey_modifiers.includes(cb.value);
  hotkeyKeySelect.value = key;
  renderKeycaps(s.hotkey_modifiers, key);
  const isDefault = isDefaultHotkey(s.hotkey_modifiers, key);
  hotkeyBadge.hidden = false;
  hotkeyBadge.textContent = isDefault ? "default" : "custom";
  hotkeyResetBtn.disabled = isDefault;
  renderHotkeyHint(s.hotkey_mode, s.hotkey_modifiers, key);
}

function showHotkeyStatus(text, cls) {
  hotkeyStatus.hidden = !text;
  hotkeyStatus.className = "status-line " + (cls || "");
  hotkeyStatus.textContent = text;
}

async function saveHotkey() {
  const modifiers = hotkeyMods.filter((cb) => cb.checked).map((cb) => cb.value);
  const resp = await postSetting("/api/settings/hotkey", { modifiers, key: hotkeyKeySelect.value });
  const s = await resp.json();
  renderHotkey(s);
  if (s.error) showHotkeyStatus("\u2715 " + s.error, "down");
  else showHotkeyStatus("\u25CF Hotkey is now " + describeHotkey(s.hotkey_modifiers, s.hotkey_key), "ok");
}

// Maps a browser KeyboardEvent to a key name the server knows: "" for a modifier
// on its own, null for keys we do not bind.
function hotkeyKeyFromEvent(e) {
  const code = e.code || "";
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  const named = { Space: "SPACE", CapsLock: "CAPS_LOCK", ScrollLock: "SCROLL_LOCK", Pause: "PAUSE",
    Insert: "INSERT", Home: "HOME", End: "END", PageUp: "PAGE_UP", PageDown: "PAGE_DOWN" };
  if (code in named) return named[code];
  if (/^(Control|Shift|Alt|Meta)(Left|Right)$/.test(code)) return "";
  return null;
}

// "Record": the next key-down in this page becomes the binding. A modifier-only combo
// is taken when the first modifier is released, because no key completes it.
function startHotkeyRecording() {
  hotkeyRecordBtn.textContent = "Press keys\u2026";
  hotkeyRecordBtn.classList.add("recording");
  let held = { ctrl: false, shift: false, alt: false, win: false };
  const finish = (key) => {
    document.removeEventListener("keydown", onDown, true);
    document.removeEventListener("keyup", onUp, true);
    hotkeyRecordBtn.textContent = "Record";
    hotkeyRecordBtn.classList.remove("recording");
    if (key === null) return;
    for (const cb of hotkeyMods) cb.checked = held[cb.value];
    hotkeyKeySelect.value = key;
    saveHotkey();
  };
  const onDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") return finish(null);
    held = { ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, win: e.metaKey };
    const key = hotkeyKeyFromEvent(e);
    if (key === null) {
      showHotkeyStatus("\u2715 That key cannot be bound; use a letter, digit, F-key or Space", "down");
      return;
    }
    if (key !== "") finish(key);
  };
  const onUp = (e) => {
    e.preventDefault();
    if (hotkeyKeyFromEvent(e) === "" && Object.values(held).some(Boolean)) finish("");
  };
  document.addEventListener("keydown", onDown, true);
  document.addEventListener("keyup", onUp, true);
}

function postSetting(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

toggle.addEventListener("change", () => postSetting("/api/settings/auto-language-detection", { enabled: toggle.checked }));
translateToggle.addEventListener("change", async () => {
  await postSetting("/api/settings/translate", { enabled: translateToggle.checked });
  loadTranslatorStatus();
});

const translatorStatus = document.getElementById("translator-status");

// Probed live so the toggle never looks like it works while nothing is listening.
async function loadTranslatorStatus() {
  translatorStatus.className = "status-line";
  translatorStatus.textContent = "Checking translator…";
  let s;
  try {
    s = await api("/api/settings/translator-status");
  } catch (err) {
    translatorStatus.className = "status-line down";
    translatorStatus.textContent = "○ Could not check the translator (" + err.message + ")";
    return;
  }
  if (s.reachable) {
    translatorStatus.className = "status-line ok";
    translatorStatus.textContent = "● Translator reachable at " + s.url;
  } else {
    translatorStatus.className = "status-line down";
    translatorStatus.textContent =
      "○ No translator at " + s.url + (translateToggle.checked ? " — text is typed untranslated" : "");
  }
}
languageSelect.addEventListener("change", () => postSetting("/api/settings/language", { language: languageSelect.value }));
micGuardToggle.addEventListener("change", () => postSetting("/api/settings/mic-guard", { enabled: micGuardToggle.checked }));
storeAudioToggle.addEventListener("change", () => postSetting("/api/settings/store-audio", { enabled: storeAudioToggle.checked }));
logFullTextToggle.addEventListener("change", () => postSetting("/api/settings/log-full-text", { enabled: logFullTextToggle.checked }));
openLogFolderButton.addEventListener("click", async () => {
  logOpenError.hidden = true;
  try {
    const r = await fetch("/api/log-folder/open", { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).error || "The log folder could not be opened.");
  } catch (e) {
    logOpenError.textContent = e.message;
    logOpenError.hidden = false;
  }
});
learnFromEditsToggle.addEventListener("change", async () => {
  await postSetting("/api/settings/learn-from-edits", { enabled: learnFromEditsToggle.checked });
  loadLearnStatus();
});

const learnStatus = document.getElementById("learn-status");
let lastSuggestedCount = null;

// Dictations happen while the dashboard stays open: refresh the status line every few
// seconds, but only while the Settings tab is shown and the page is visible.
setInterval(() => {
  if (!document.hidden && document.getElementById("tab-settings").classList.contains("active")) loadLearnStatus();
}, 3000);

// Why the last paste did or did not learn, so "no suggestions" never looks like a silent failure.
async function loadLearnStatus() {
  let s;
  try {
    s = await api("/api/settings/learn-from-edits-status");
  } catch (err) {
    learnStatus.textContent = "";
    return;
  }
  const counts = s.counts || {};
  // A watch that just ended with suggestions: show them without a reload.
  if (lastSuggestedCount !== null && (counts.suggested || 0) > lastSuggestedCount) loadDictionarySuggestions();
  lastSuggestedCount = counts.suggested || 0;
  // Outcomes of a watch that ran to its end; everything else is a paste that was skipped.
  const watchedKeys = new Set(s.watched || []);
  const watched = Object.entries(counts).filter(([k]) => watchedKeys.has(k)).reduce((n, [, c]) => n + c, 0);
  const skipped = Object.entries(counts)
    .filter(([k]) => !watchedKeys.has(k))
    .sort((a, b) => b[1] - a[1]);
  const skippedTotal = skipped.reduce((n, [, c]) => n + c, 0);
  learnStatus.className = "status-line" + (s.last && s.last.outcome === "suggested" ? " ok" : "");
  if (!s.last) {
    learnStatus.textContent = learnFromEditsToggle.checked ? "No paste watched since NexusVox started." : "";
    return;
  }
  const time = new Date(s.last.at * 1000).toLocaleTimeString();
  let text = "Last paste (" + time + "): " + s.last.message + ". Since start: " + watched + " watched, " +
    (counts.suggested || 0) + " with suggestions, " + skippedTotal + " skipped";
  if (skippedTotal) text += " (" + skipped.map(([k, c]) => k.replace("_", " ") + " " + c).join(", ") + ")";
  learnStatus.textContent = text + ".";
}
polishTerminalsToggle.addEventListener("change", () => postSetting("/api/settings/polish-terminals", { enabled: polishTerminalsToggle.checked }));
hotkeyModeSelect.addEventListener("change", async () => {
  const resp = await postSetting("/api/settings/hotkey-mode", { mode: hotkeyModeSelect.value });
  const s = await resp.json();
  hotkeyModeSelect.value = s.hotkey_mode;
  renderHotkeyHint(s.hotkey_mode, s.hotkey_modifiers, s.hotkey_key);
});
for (const cb of hotkeyMods) cb.addEventListener("change", saveHotkey);
hotkeyKeySelect.addEventListener("change", saveHotkey);
polishKeySelect.addEventListener("change", async () => {
  const resp = await postSetting("/api/settings/polish-key", { key: polishKeySelect.value });
  const s = await resp.json();
  if (s.error) alert(s.error);
  polishKeySelect.value = s.polish_key;
});
llmModelInput.addEventListener("change", async () => {
  const resp = await postSetting("/api/settings/llm-model", { model: llmModelInput.value });
  const s = await resp.json();
  if (s.error) alert(s.error);
  llmModelInput.value = s.llm_model;
  loadLlmStatus();
});
meetingOwnLabelInput.addEventListener("change", async () => {
  try {
    const resp = await postSetting("/api/settings/meeting-own-voice-label", { label: meetingOwnLabelInput.value });
    const s = await resp.json();
    if (s.error) alert(s.error);
    meetingOwnLabelInput.value = meetingOwnLabelSaved = s.meeting_own_voice_label;
  } catch (err) {
    alert("The name could not be saved.");
    meetingOwnLabelInput.value = meetingOwnLabelSaved;
  }
});
feedbackModeSelect.addEventListener("change", async () => {
  const resp = await postSetting("/api/settings/feedback-mode", { mode: feedbackModeSelect.value });
  const s = await resp.json();
  if (s.error) alert(s.error);
  feedbackModeSelect.value = s.feedback_mode;
});
cleanupLevelSelect.addEventListener("change", async () => {
  const resp = await postSetting("/api/settings/cleanup-level", { level: cleanupLevelSelect.value });
  const s = await resp.json();
  if (s.error) alert(s.error);
  cleanupLevelSelect.value = s.cleanup_level;
});
pasteKeySelect.addEventListener("change", async () => {
  const resp = await postSetting("/api/settings/paste-key", { key: pasteKeySelect.value });
  const s = await resp.json();
  if (s.error) alert(s.error);
  pasteKeySelect.value = s.paste_key;
});
hotkeyRecordBtn.addEventListener("click", startHotkeyRecording);
hotkeyResetBtn.addEventListener("click", () => {
  for (const cb of hotkeyMods) cb.checked = hotkeyDefault.modifiers.includes(cb.value);
  hotkeyKeySelect.value = hotkeyDefault.key;
  saveHotkey();
});

async function loadSettings() {
  const s = await api("/api/settings");
  toggle.checked = s.auto_language_detection;
  translateToggle.checked = s.translate_enabled;
  languageSelect.value = s.language;
  hotkeyModeSelect.value = s.hotkey_mode;
  renderHotkey(s);
  if (!pasteKeySelect.options.length) {
    pasteKeySelect.append(new Option("Off", ""));
    for (const k of s.hotkey_key_options || []) pasteKeySelect.append(new Option(k.replace("_", " "), k));
  }
  pasteKeySelect.value = s.paste_key;
  if (!polishKeySelect.options.length) {
    polishKeySelect.append(new Option("Off", ""));
    for (const k of s.hotkey_key_options || []) polishKeySelect.append(new Option(k.replace("_", " "), k));
  }
  polishKeySelect.value = s.polish_key;
  llmModelInput.value = s.llm_model;
  meetingOwnLabelInput.value = meetingOwnLabelSaved = s.meeting_own_voice_label;
  cleanupLevelSelect.value = s.cleanup_level;
  cleanupLevelSelect.disabled = !!s.cleanup_needs_cuda;
  cleanupLevelSelect.title = s.cleanup_needs_cuda ? "Needs a CUDA GPU" : "";
  document.getElementById("cleanup-cuda-hint").hidden = !s.cleanup_needs_cuda;
  feedbackModeSelect.value = s.feedback_mode;
  loadLlmStatus();
  micGuardToggle.checked = s.mic_guard_enabled;
  storeAudioToggle.checked = s.store_audio;
  logFullTextToggle.checked = s.log_full_text;
  logDirLabel.textContent = s.log_dir;
  learnFromEditsToggle.checked = s.learn_from_edits;
  loadLearnStatus();
  polishTerminalsToggle.checked = s.polish_in_terminals;
  loadTranslatorStatus();
  await Promise.all([loadDeviceSettings(), loadModelSwitcher(), loadOsCommands(), loadVoiceCommands(), loadDictionary(), loadDictionarySuggestions(), loadSnippets(), loadPolishPresets()]);
}

// ── Correction Dictionary ────────────────────────────────────────────
async function loadDictionary() {
  const { entries } = await fetch("/api/dictionary").then((r) => r.json());
  const list = document.getElementById("dict-list");
  if (!entries.length) {
    list.innerHTML = '<p class="nexus-empty">No entries yet.</p>';
    return;
  }
  list.innerHTML = entries.map((e) => `
    <div class="dict-row" data-id="${e.id}">
      <span class="dict-wrong">${escapeHtml(e.wrong)}</span>
      <span class="dict-arrow">&rarr;</span>
      <span class="dict-right">${escapeHtml(e.right)}</span>
      <button class="dict-delete" title="Remove" onclick="deleteDictionaryEntry(${e.id})">&#10005;</button>
    </div>`).join("");
}

async function addDictionaryEntry(wrong, right) {
  const res = await fetch("/api/dictionary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wrong, right }),
  }).then((r) => r.json());
  if (res.ok) loadDictionary();
  return res.ok;
}

async function deleteDictionaryEntry(id) {
  await fetch(`/api/dictionary/${id}`, { method: "DELETE" });
  loadDictionary();
}

// ── Snippets ─────────────────────────────────────────────────────────
let snippetCache = [];

// The options box holds one `name = text` per line; the API takes [{name, text}].
function parseSnippetOptions(lines) {
  return lines.split("\n").flatMap((line) => {
    const i = line.indexOf("=");
    const name = line.slice(0, i).trim();
    const text = line.slice(i + 1).trim();
    return i > 0 && name && text ? [{ name, text }] : [];
  });
}

function formatSnippetOptions(options) {
  return options.map((o) => `${o.name} = ${o.text}`).join("\n");
}

async function loadSnippets() {
  const { snippets } = await fetch("/api/snippets").then((r) => r.json());
  snippetCache = snippets;
  const list = document.getElementById("snippet-list");
  if (!snippets.length) {
    list.innerHTML = '<p class="nexus-empty">No snippets yet.</p>';
    return;
  }
  list.innerHTML = snippets.map((s) => `
    <div class="dict-row" data-id="${s.id}">
      <span class="dict-right">${escapeHtml(s.trigger)}</span>
      <span class="dict-arrow">&rarr;</span>
      <span class="snippet-text">${escapeHtml(s.options.length ? `${s.options.length} options: ${s.options.map((o) => o.name).join(", ")}` : s.expansion)}</span>
      <button class="flagged-play-btn" onclick="editSnippet(${s.id})">Edit</button>
      <button class="dict-delete" title="Remove" onclick="deleteSnippet(${s.id})">&#10005;</button>
    </div>`).join("");
}

function resetSnippetForm() {
  document.getElementById("snippet-id").value = "";
  document.getElementById("snippet-trigger").value = "";
  document.getElementById("snippet-expansion").value = "";
  document.getElementById("snippet-options").value = "";
  document.getElementById("snippet-save").textContent = "Add";
  document.getElementById("snippet-cancel").hidden = true;
}

function editSnippet(id) {
  const s = snippetCache.find((x) => x.id === id);
  if (!s) return;
  document.getElementById("snippet-id").value = s.id;
  document.getElementById("snippet-trigger").value = s.trigger;
  document.getElementById("snippet-expansion").value = s.expansion;
  document.getElementById("snippet-options").value = formatSnippetOptions(s.options);
  document.getElementById("snippet-save").textContent = "Save";
  document.getElementById("snippet-cancel").hidden = false;
  document.getElementById("snippet-expansion").focus();
}

async function deleteSnippet(id) {
  await fetch(`/api/snippets/${id}`, { method: "DELETE" });
  loadSnippets();
}

document.getElementById("snippet-cancel").addEventListener("click", resetSnippetForm);

document.getElementById("snippet-github").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = "Loading...";
  const res = await fetch("/api/snippets/github-repos").then((r) => r.json());
  btn.disabled = false;
  btn.textContent = "Fill from GitHub";
  if (!res.ok) {
    alert(res.error);
    return;
  }
  const box = document.getElementById("snippet-options");
  const lines = formatSnippetOptions(res.options);
  box.value = box.value.trim() ? `${box.value.trim()}\n${lines}` : lines;
});

document.getElementById("snippet-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("snippet-id").value;
  const res = await fetch("/api/snippets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: id ? Number(id) : null,
      trigger: document.getElementById("snippet-trigger").value,
      expansion: document.getElementById("snippet-expansion").value,
      options: parseSnippetOptions(document.getElementById("snippet-options").value),
    }),
  }).then((r) => r.json());
  if (res.ok) {
    resetSnippetForm();
    loadSnippets();
  } else {
    alert(res.error);
  }
});

// ── Polish presets ───────────────────────────────────────────────────
let presetCache = [];

async function loadPolishPresets() {
  const { presets } = await fetch("/api/polish-presets").then((r) => r.json());
  presetCache = presets;
  const list = document.getElementById("preset-list");
  if (!presets.length) {
    list.innerHTML = '<p class="nexus-empty">No presets yet.</p>';
    return;
  }
  list.innerHTML = presets.map((p) => `
    <div class="dict-row" data-id="${p.id}">
      <span class="dict-wrong">${escapeHtml(p.name)}${p.is_default ? " (default)" : ""}</span>
      <span class="snippet-text">${escapeHtml(p.instruction)}</span>
      <button class="flagged-play-btn" onclick="editPolishPreset(${p.id})">Edit</button>
      <button class="dict-delete" title="Remove" onclick="deletePolishPreset(${p.id})">&#10005;</button>
    </div>`).join("");
}

function resetPresetForm() {
  document.getElementById("preset-id").value = "";
  document.getElementById("preset-name").value = "";
  document.getElementById("preset-instruction").value = "";
  document.getElementById("preset-default").checked = false;
  document.getElementById("preset-save").textContent = "Add";
  document.getElementById("preset-cancel").hidden = true;
}

function editPolishPreset(id) {
  const p = presetCache.find((x) => x.id === id);
  if (!p) return;
  document.getElementById("preset-id").value = p.id;
  document.getElementById("preset-name").value = p.name;
  document.getElementById("preset-instruction").value = p.instruction;
  document.getElementById("preset-default").checked = p.is_default;
  document.getElementById("preset-save").textContent = "Save";
  document.getElementById("preset-cancel").hidden = false;
  document.getElementById("preset-instruction").focus();
}

async function deletePolishPreset(id) {
  await fetch(`/api/polish-presets/${id}`, { method: "DELETE" });
  if (document.getElementById("preset-id").value === String(id)) resetPresetForm();
  loadPolishPresets();
}

document.getElementById("preset-cancel").addEventListener("click", resetPresetForm);

document.getElementById("preset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("preset-id").value;
  const res = await fetch("/api/polish-presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: id ? Number(id) : null,
      name: document.getElementById("preset-name").value,
      instruction: document.getElementById("preset-instruction").value,
      is_default: document.getElementById("preset-default").checked,
    }),
  }).then((r) => r.json());
  if (res.ok) {
    resetPresetForm();
    loadPolishPresets();
  } else {
    alert(res.error);
  }
});

async function loadDictionarySuggestions() {
  const { suggestions } = await fetch("/api/dictionary/suggestions").then((r) => r.json());
  const box = document.getElementById("dict-suggestions");
  box.innerHTML = suggestions.length ? "<p class=\"settings-hint\">Learned from your edits:</p>" + suggestions.map((s) => `
    <div class="dict-row" data-id="${s.id}">
      <span class="dict-wrong">${escapeHtml(s.wrong)}</span>
      <span class="dict-arrow">&rarr;</span>
      <span class="dict-right">${escapeHtml(s.right)}</span>
      <button class="flagged-play-btn" onclick="acceptDictionarySuggestion(${s.id})">Add</button>
      <button class="dict-delete" title="Dismiss" onclick="dismissDictionarySuggestion(${s.id})">&#10005;</button>
    </div>`).join("") : "";
}

async function acceptDictionarySuggestion(id) {
  await fetch(`/api/dictionary/suggestions/${id}/accept`, { method: "POST" });
  loadDictionary();
  loadDictionarySuggestions();
}

async function dismissDictionarySuggestion(id) {
  await fetch(`/api/dictionary/suggestions/${id}`, { method: "DELETE" });
  loadDictionarySuggestions();
}

document.getElementById("dict-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const wrongEl = document.getElementById("dict-wrong");
  const rightEl = document.getElementById("dict-right");
  if (await addDictionaryEntry(wrongEl.value, rightEl.value)) {
    wrongEl.value = "";
    rightEl.value = "";
    wrongEl.focus();
  }
});

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

const vcPressEnterToggle = document.getElementById("voice-press-enter-toggle");

vcPressEnterToggle.addEventListener("change", async () => {
  await fetch("/api/voice-commands/press-enter", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: vcPressEnterToggle.checked }),
  });
});

vcBypassSymbolsToggle.addEventListener("change", async () => {
  await fetch("/api/voice-commands/bypass-symbols", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: vcBypassSymbolsToggle.checked }),
  });
});

const vcBypassStructuralToggle = document.getElementById("voice-bypass-structural-toggle");

vcBypassStructuralToggle.addEventListener("change", async () => {
  await fetch("/api/voice-commands/bypass-structural", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: vcBypassStructuralToggle.checked }),
  });
});

async function loadVoiceCommands() {
  const data = await fetch("/api/voice-commands").then((r) => r.json());
  vcToggle.checked = data.enabled;
  vcNumbersToggle.checked = data.numbers_as_digits;
  vcPressEnterToggle.checked = data.press_enter;
  vcBypassSymbolsToggle.checked = data.bypass_symbols;
  vcBypassStructuralToggle.checked = data.bypass_structural;
  renderSymbolGrid(data.all_symbols, data.symbols);
  renderStructuralGrid(data.all_structural, data.structural);
}

function renderStructuralGrid(allStructural, activeStructural) {
  const activeSet = new Set(activeStructural);
  const grid = document.getElementById("voice-structural-grid");
  grid.innerHTML = allStructural.map((s) => {
    const checked = activeSet.has(s.keyword) ? "checked" : "";
    const alias = s.aliases.length ? ` title="${escapeHtml(s.aliases.join(", "))}"` : "";
    return `<label class="symbol-chip"${alias}>` +
      `<input type="checkbox" class="structural-checkbox" data-keyword="${escapeHtml(s.keyword)}" ${checked}>` +
      `<code>${escapeHtml(s.keyword)}</code>` +
      `<span class="symbol-char">${escapeHtml(s.char)}</span>` +
      `</label>`;
  }).join("");

  grid.querySelectorAll(".structural-checkbox").forEach((cb) => {
    cb.addEventListener("change", saveStructural);
  });
}

async function saveStructural() {
  const structural = [...document.querySelectorAll(".structural-checkbox:checked")]
    .map((cb) => cb.dataset.keyword);
  await fetch("/api/voice-commands/structural", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ structural }),
  });
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
  } else if (m.inprocess_supported && !m.requires_gpu) {
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
  const [overview, lang, hours, words, heatmap, models, apps] = await Promise.all([
    api("/api/overview"),
    api("/api/language-distribution"),
    api("/api/peak-usage-hours"),
    api("/api/top-words?n=20"),
    api("/api/activity-heatmap"),
    api("/api/model-breakdown"),
    api("/api/app-breakdown"),
  ]);

  renderOverview(overview);
  renderLanguage(lang);
  renderHours(hours);
  renderWords(words);
  renderHeatmap(heatmap);
  renderModelBreakdown(models);
  renderAppBreakdown(apps);
  loadTimeline();
}

async function loadTimeline() {
  const [timeline, latency, accuracy] = await Promise.all([
    api("/api/transcriptions-over-time?period=" + currentPeriod),
    api("/api/latency-trend?period=" + currentPeriod),
    api("/api/accuracy?period=" + currentPeriod),
  ]);
  renderTimeline(timeline);
  renderLatency(latency);
  renderAccuracy(accuracy);
}

// ── Render Functions ─────────────────────────────────────────────────
function renderOverview(d) {
  document.getElementById("stat-total").textContent = d.total_transcriptions;
  document.getElementById("stat-words").textContent = d.total_words.toLocaleString();
  document.getElementById("stat-wpm").textContent = d.avg_wpm;
  document.getElementById("stat-wpm-label").textContent =
    d.total_transcriptions ? `Avg WPM (${Math.round(d.min_wpm)} to ${Math.round(d.max_wpm)})` : "Avg WPM";
  document.getElementById("stat-duration").textContent = formatDuration(d.avg_duration_ms);
  document.getElementById("stat-longest").textContent = formatDuration(d.longest_duration_ms);
  document.getElementById("stat-longest-label").textContent =
    d.longest_words ? `Longest (${d.longest_words} words)` : "Longest Dictation";
  document.getElementById("stat-transcribe").textContent =
    d.avg_transcribe_ms != null ? formatDuration(d.avg_transcribe_ms) : "--";
  document.getElementById("stat-transcribe-label").textContent =
    d.avg_rtf != null ? `Transcribe (${d.avg_rtf}x real time)` : "Transcribe Time";
  document.getElementById("stat-latency").textContent =
    d.avg_latency_ms != null ? formatDuration(d.avg_latency_ms) : "--";
  document.getElementById("stat-saved").textContent = d.time_saved_minutes;
  document.getElementById("stat-translated").textContent = d.translated_count;
  const parts = [];
  if (d.avg_translate_ms != null) parts.push("avg " + formatDuration(d.avg_translate_ms));
  if (d.translate_failures) parts.push(d.translate_failures + " fell back");
  document.getElementById("stat-translated-label").textContent =
    "Translated" + (parts.length ? " (" + parts.join(", ") + ")" : "");

  document.getElementById("stat-cleaned").textContent = d.cleanup_applied;
  const cleanParts = [];
  if (d.avg_cleanup_ms != null) cleanParts.push("avg " + formatDuration(d.avg_cleanup_ms));
  if (d.cleanup_timeouts) cleanParts.push(d.cleanup_timeouts + " timed out");
  if (d.cleanup_rejected) cleanParts.push(d.cleanup_rejected + " rejected");
  if (d.cleanup_failed) cleanParts.push(d.cleanup_failed + " failed");
  document.getElementById("stat-cleaned-label").textContent =
    "Cleaned Up" + (cleanParts.length ? " (" + cleanParts.join(", ") + ")" : "");

  document.getElementById("stat-uploads").textContent = d.upload_count;
  document.getElementById("stat-upload-hours").textContent = d.upload_audio_hours;
  document.getElementById("stat-upload-processing").textContent =
    d.avg_upload_processing_ms != null ? formatDuration(d.avg_upload_processing_ms) : "--";
  document.getElementById("stat-upload-processing-label").textContent =
    d.upload_rtf != null ? `Upload Processing (${d.upload_rtf}x real time)` : "Upload Processing";
  document.getElementById("stat-upload-speakers").textContent =
    d.avg_upload_speakers != null ? d.avg_upload_speakers : "--";
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
        backgroundColor: COLORS.accentFill,
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
        backgroundColor: [COLORS.accent, COLORS.green, COLORS.orange, COLORS.red, COLORS.accentLight],
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
          return v === max ? COLORS.accent : COLORS.accentSoft;
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
        ? withAlpha(COLORS.accent, 0.05)
        : withAlpha(COLORS.accent, 0.15 + intensity * 0.75);
      html += '<td style="background:' + bg + '">' + (val || "") + "</td>";
    });
    html += "</tr>";
  });

  html += "</tbody></table>";
  container.innerHTML = html;
}

// ── Latency Trend Chart ──────────────────────────────────────────────
function renderLatency(d) {
  if (charts.latency) charts.latency.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-latency").getContext("2d");
  const pointRadius = d.labels.length > 30 ? 0 : 3;
  charts.latency = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.labels,
      datasets: [
        {
          label: "Transcribe",
          data: d.transcribe_ms,
          borderColor: COLORS.green,
          backgroundColor: COLORS.greenFill,
          fill: true,
          tension: 0.3,
          pointRadius,
          pointBackgroundColor: COLORS.green,
        },
        {
          label: "Release to text",
          data: d.latency_ms,
          borderColor: COLORS.accent,
          backgroundColor: "transparent",
          tension: 0.3,
          pointRadius,
          pointBackgroundColor: COLORS.accent,
        },
      ],
    },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, ticks: { ...chartDefaults.scales.y.ticks, callback: (v) => formatDuration(v) } },
      },
      plugins: {
        legend: { display: true, labels: { color: COLORS.dim, boxWidth: 12 } },
        tooltip: {
          mode: "index",
          intersect: false,
          callbacks: { label: (c) => c.dataset.label + ": " + (c.parsed.y != null ? formatDuration(c.parsed.y) : "--") },
        },
      },
    },
  });
}

// ── Per-Model Table ──────────────────────────────────────────────────
function renderModelBreakdown(rows) {
  const body = document.getElementById("model-breakdown");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-state">No transcriptions yet.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(
      (m) => `<tr>
        <td>${escapeHtml(m.model)}</td>
        <td>${m.count}</td>
        <td>${m.words.toLocaleString()}</td>
        <td>${m.avg_wpm}</td>
        <td>${formatDuration(m.avg_duration_ms)}</td>
        <td>${m.avg_transcribe_ms != null ? formatDuration(m.avg_transcribe_ms) : "--"}</td>
      </tr>`
    )
    .join("");
}

// ── Per-App Table ────────────────────────────────────────────────────
function renderAppBreakdown(rows) {
  const body = document.getElementById("app-breakdown");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty-state">No transcriptions yet.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(
      (a) => `<tr>
        <td>${escapeHtml(a.app)}</td>
        <td>${a.count}</td>
        <td>${a.words.toLocaleString()}</td>
      </tr>`
    )
    .join("");
}

// ── Accuracy and Dictionary Stats ────────────────────────────────────
function renderAccuracy(d) {
  if (charts.accuracy) charts.accuracy.destroy();
  document.getElementById("accuracy-summary").textContent = d.words
    ? `${d.corrections.toLocaleString()} corrections in ${d.words.toLocaleString()} dictated words. ` +
      "Counted from reviewed transcripts and suggestions you accepted."
    : "";
  if (d.labels.length) {
    const ctx = document.getElementById("chart-accuracy").getContext("2d");
    charts.accuracy = new Chart(ctx, {
      type: "line",
      data: {
        labels: d.labels,
        datasets: [{
          data: d.values,
          borderColor: COLORS.orange,
          backgroundColor: "transparent",
          tension: 0.3,
          pointRadius: d.labels.length > 30 ? 0 : 3,
          pointBackgroundColor: COLORS.orange,
        }],
      },
      options: {
        ...chartDefaults,
        plugins: { ...chartDefaults.plugins, tooltip: { mode: "index", intersect: false } },
      },
    });
  }

  const learn = d.learn;
  document.getElementById("learn-summary").textContent = learn.watched || learn.skipped || learn.accepted
    ? `${learn.watched} watched, ${learn.skipped} skipped. ${learn.suggested} led to suggestions, ${learn.accepted} accepted.`
    : "";
  document.getElementById("learn-outcomes").innerHTML = learn.outcomes.length
    ? learn.outcomes
        .map((o) => `<tr><td>${escapeHtml(o.message)}</td><td>${o.count}</td></tr>`)
        .join("")
    : '<tr><td colspan="2" class="empty-state">No pastes watched yet.</td></tr>';

  document.getElementById("most-corrected").innerHTML = d.most_corrected.length
    ? d.most_corrected
        .map(
          (c) => `<tr>
            <td>${escapeHtml(c.wrong)}</td>
            <td>${escapeHtml(c.right)}</td>
            <td>${c.count}</td>
          </tr>`
        )
        .join("")
    : '<tr><td colspan="3" class="empty-state">No corrections yet.</td></tr>';

  const dict = d.dictionary;
  document.getElementById("dictionary-summary").textContent = dict.items.length
    ? `${dict.entries} entries now. ${dict.total_hits.toLocaleString()} applications in total, removed entries included.`
    : "";
  document.getElementById("dictionary-usage").innerHTML = dict.items.length
    ? dict.items
        .map(
          (e) => `<tr>
            <td>${escapeHtml(e.wrong)}</td>
            <td>${escapeHtml(e.right)}${e.removed ? " (removed)" : ""}</td>
            <td>${e.hits}</td>
            <td>${e.last_used ? escapeHtml(e.last_used) : "--"}</td>
          </tr>`
        )
        .join("")
    : '<tr><td colspan="4" class="empty-state">The dictionary is empty.</td></tr>';
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
// Keyboard-driven review: the top card is "active". Space plays its audio,
// Enter accepts it, E opens the correction textarea (Ctrl+Enter submits,
// Esc reverts). After each submit the next card becomes active and autoplays.
let reviewSessionCount = 0;
let reviewRemainingOlder = 0;
let reviewAudio = null;

async function loadReviewTranscriptions() {
  const container = document.getElementById("review-list");
  container.innerHTML = "";
  reviewSessionCount = 0;
  await loadMoreReviewTranscriptions();
}

function updateReviewProgress() {
  const onScreen = document.querySelectorAll("#review-list .flagged-card[data-id]").length;
  const el = document.getElementById("review-progress");
  el.textContent = `${reviewSessionCount} reviewed this session \u00B7 ${onScreen + reviewRemainingOlder} left`;
}

function activeReviewCard() {
  return document.querySelector("#review-list .flagged-card[data-id]");
}

function activateTopReviewCard(autoplay) {
  document.querySelectorAll("#review-list .review-active").forEach((c) => c.classList.remove("review-active"));
  const card = activeReviewCard();
  if (!card) return;
  card.classList.add("review-active");
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (autoplay) toggleReviewAudio(card);
}

function stopReviewAudio() {
  if (!reviewAudio) return;
  reviewAudio.audio.pause();
  reviewAudio.btn.textContent = "\u25B6 Play";
  reviewAudio = null;
}

function toggleReviewAudio(card) {
  const btn = card.querySelector(".flagged-play-btn");
  if (reviewAudio && reviewAudio.btn === btn) {
    stopReviewAudio();
    return;
  }
  stopReviewAudio();
  const audio = new Audio(`/api/audio/${card.dataset.id}`);
  reviewAudio = { audio, btn };
  btn.textContent = "\u25A0 Stop";
  const reset = () => { if (reviewAudio && reviewAudio.audio === audio) stopReviewAudio(); };
  audio.onended = reset;
  audio.onerror = reset;
  audio.play().catch(reset);
}

async function retryReviewTranscription(card, btn) {
  btn.disabled = true;
  btn.textContent = "Transcribing...";
  let res;
  try {
    res = await fetch(`/api/review/${card.dataset.id}/retry`, { method: "POST" }).then((r) => r.json());
  } catch (err) {
    res = { ok: false, error: `Retry failed: ${err}` };
  } finally {
    btn.disabled = false;
    btn.textContent = "\u21BB Retry";
  }
  if (!res.ok) {
    alert(res.error);
    return;
  }
  // The new transcript goes into the correction box; submitting it is the user's call.
  markReviewWrong(card);
  card.querySelector(".review-correction").value = res.text;
}

function markReviewWrong(card) {
  const cb = card.querySelector(".review-correct-cb");
  cb.checked = false;
  toggleReviewTextarea(cb);
  card.querySelector(".review-correction").focus();
}

function markReviewCorrect(card) {
  const cb = card.querySelector(".review-correct-cb");
  cb.checked = true;
  toggleReviewTextarea(cb);
  card.querySelector(".review-correction").blur();
}

document.addEventListener("keydown", (e) => {
  if (!document.getElementById("tab-review").classList.contains("active")) return;
  const card = activeReviewCard();
  if (!card) return;
  const inTextarea = e.target === card.querySelector(".review-correction");

  if (inTextarea) {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      submitReview(card.dataset.id, card.querySelector(".flagged-save-btn"));
    } else if (e.key === "Escape") {
      e.preventDefault();
      markReviewCorrect(card);
    }
    return;
  }
  if (e.target.matches("input, textarea, select") || e.ctrlKey || e.altKey || e.metaKey) return;

  if (e.key === " ") {
    e.preventDefault();
    toggleReviewAudio(card);
  } else if (e.key === "Enter") {
    e.preventDefault();
    markReviewCorrect(card);
    submitReview(card.dataset.id, card.querySelector(".flagged-save-btn"));
  } else if (e.key === "e" || e.key === "E") {
    e.preventDefault();
    markReviewWrong(card);
  }
});

async function loadMoreReviewTranscriptions() {
  const container = document.getElementById("review-list");
  const loadMore = document.getElementById("review-load-more");
  const cards = container.querySelectorAll(".flagged-card[data-id]");
  const lastId = cards.length ? cards[cards.length - 1].dataset.id : null;
  const url = lastId ? `/api/review?before_id=${lastId}` : "/api/review";

  loadMore.disabled = true;
  const { items: data, remaining } = await fetch(url).then((r) => r.json());

  loadMore.hidden = remaining === 0;
  loadMore.disabled = false;
  loadMore.textContent = `Load more (${remaining} older)`;
  reviewRemainingOlder = remaining;

  if (!data.length && !cards.length) {
    container.innerHTML = '<p class="empty-state">No unreviewed recordings. All caught up!</p>';
    updateReviewProgress();
    return;
  }

  container.insertAdjacentHTML("beforeend", data.map((item) => {
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
      <button class="flagged-play-btn" onclick="toggleReviewAudio(this.closest('.flagged-card'))">&#9654; Play</button>
      <button class="flagged-play-btn review-retry-btn" title="Transcribe the stored audio again with the current model"
        onclick="retryReviewTranscription(this.closest('.flagged-card'), this)">&#8635; Retry</button>
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
  }).join(""));
  updateReviewProgress();
  if (!cards.length) activateTopReviewCard(false);
}

function showReviewSuggestions(suggestions) {
  const box = document.getElementById("review-suggestions");
  box.innerHTML = "<span>Add to dictionary?</span>" + suggestions.map((sg) => `
    <button class="flagged-play-btn" onclick="acceptReviewSuggestion(this)"
      data-wrong="${escapeHtml(sg.wrong)}" data-right="${escapeHtml(sg.right)}">
      <s>${escapeHtml(sg.wrong)}</s> &rarr; ${escapeHtml(sg.right)}
    </button>`).join("") + '<button class="flagged-play-btn" onclick="dismissReviewSuggestions()">Dismiss</button>';
  box.hidden = false;
}

async function acceptReviewSuggestion(btn) {
  btn.disabled = true;
  if (await addDictionaryEntry(btn.dataset.wrong, btn.dataset.right)) {
    btn.textContent = "Added";
  } else {
    btn.disabled = false;
  }
}

function dismissReviewSuggestions() {
  const box = document.getElementById("review-suggestions");
  box.hidden = true;
  box.innerHTML = "";
}

function toggleReviewTextarea(cb) {
  const card = cb.closest(".flagged-card");
  card.querySelector(".review-correction").style.display = cb.checked ? "none" : "block";
}

async function submitReview(id, btn) {
  const card = btn.closest(".flagged-card");
  const isCorrect = card.querySelector(".review-correct-cb").checked;
  const correctedText = card.querySelector(".review-correction").value || null;

  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = "Submitting...";
  stopReviewAudio();
  try {
    const res = await fetch(`/api/review/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_correct: isCorrect, corrected_text: isCorrect ? null : correctedText }),
    });
    const data = await res.json();
    if (data.ok) {
      reviewSessionCount += 1;
      if (data.suggestions && data.suggestions.length) showReviewSuggestions(data.suggestions);
      card.style.transition = "opacity 0.3s";
      card.style.opacity = "0";
      setTimeout(async () => {
        card.remove();
        if (!document.getElementById("review-list").children.length) {
          await loadMoreReviewTranscriptions();
        }
        updateReviewProgress();
        activateTopReviewCard(true);
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

function formatClock(seconds) {
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
      uploadSelectedMeta.textContent = sizeText + " · " + formatClock(probe.duration);
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
cleanupToggle.addEventListener("change", syncCleanupLevel);
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

function showUploadError(message) {
  uploadErrorEl.textContent = message;
  uploadErrorEl.style.display = "block";
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
async function downloadExport(url) {
  uploadErrorEl.style.display = "none";
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showUploadError(body.error || `Export failed (${res.status})`);
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
    showUploadError(`Export failed: ${err.message}`);
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
  uploadErrorEl.style.display = "none";
  uploadNoteEl.style.display = "none";
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
        showUploadError(body.error || `${action.label} failed (${res.status})`);
      } else if (body.note || body.message) {
        uploadNoteEl.textContent = body.note || body.message;
        uploadNoteEl.style.display = "block";
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
    showUploadError(`${action.label} failed: ${err.message}`);
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
      downloadExport(link.href);
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
      note.className = "status-line down";
      note.textContent = "✕ " + error;
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
    <span class="status-line down upload-delete-error" hidden></span>`;
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
      err.textContent = "✕ " + (result.error || "Delete failed");
      err.hidden = false;
      event.target.disabled = false;
      return;
    }
    loadFileTranscriptions();
  });
}

// ── Meetings ─────────────────────────────────────────────────────────
const meetingToggleBtn = document.getElementById("meeting-toggle");
const meetingTimer = document.getElementById("meeting-timer");
const meetingIndicator = document.getElementById("meeting-indicator");
const meetingStatus = document.getElementById("meeting-status");
const meetingProgress = document.getElementById("meeting-progress");
const meetingProgressBar = document.getElementById("meeting-progress-bar");
const meetingConsent = document.getElementById("meeting-consent");
const meetingConsentToggle = document.getElementById("meeting-consent-toggle");
const meetingDictationToggle = document.getElementById("meeting-dictation-toggle");
const meetingLiveToggle = document.getElementById("meeting-live-toggle");
const meetingStoreAudioToggle = document.getElementById("meeting-store-audio-toggle");
const meetingLiveBox = document.getElementById("meeting-live");
const meetingLiveText = document.getElementById("meeting-live-text");
const meetingLiveStatus = document.getElementById("meeting-live-status");
let meetingLiveTimer = null;
let meetingLiveAfter = 0;
let meetingLiveBusy = false;
let meetingLiveGeneration = 0;
const meetingHotkeyMods = Array.from(document.querySelectorAll("#meeting-hotkey-mods input"));
const meetingHotkeyKey = document.getElementById("meeting-hotkey-key");
const meetingHotkeyStatus = document.getElementById("meeting-hotkey-status");
const meetingMicSelect = document.getElementById("meeting-mic-select");
const meetingOutputSelect = document.getElementById("meeting-output-select");
let meetingRecording = false;
let meetingElapsed = 0;
let meetingTick = null;
let meetingPoll = null;
let meetingJobTimer = null;
let meetingConsentHint = true;

function formatClock(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${m}:${s}` : `${m}:${s}`;
}

function showMeetingStatus(text, cls) {
  meetingStatus.hidden = !text;
  meetingStatus.className = "status-line " + (cls || "");
  meetingStatus.textContent = text;
}

// Live transcript: poll the new segments every 2 s while a meeting records. Text goes in with textContent only.
function appendLiveSegments(segments) {
  const atBottom = meetingLiveText.scrollTop + meetingLiveText.clientHeight >= meetingLiveText.scrollHeight - 8;
  for (const seg of segments) {
    if (seg.n < meetingLiveAfter) continue; // already shown (overlapping polls)
    const p = document.createElement("p");
    const who = document.createElement("span");
    who.className = "who " + (seg.speaker === "mic" ? "me" : "system");
    who.textContent = seg.label + ":";
    p.append(who, document.createTextNode(seg.text));
    meetingLiveText.append(p);
    meetingLiveAfter = seg.n + 1;
  }
  if (atBottom) meetingLiveText.scrollTop = meetingLiveText.scrollHeight;
}

async function pollMeetingLive() {
  if (meetingLiveBusy) return; // one request at a time: two in flight would both append the same segments
  meetingLiveBusy = true;
  const generation = meetingLiveGeneration;
  try {
    const live = await fetch("/api/meetings/live?after=" + meetingLiveAfter).then((r) => r.json());
    if (generation !== meetingLiveGeneration) return; // a new meeting started while this was in flight
    // A meeting stopped from the hotkey while another tab is shown never reaches renderMeetingState.
    // The poll runs on until the worker has ended too: a window in flight at Stop still adds its text.
    if (!live.recording && !(live.status && live.status.active) && meetingLiveTimer) {
      clearInterval(meetingLiveTimer);
      meetingLiveTimer = null;
    }
    meetingLiveBox.hidden = !live.status;
    appendLiveSegments(live.segments);
    if (live.status) {
      const st = live.status;
      meetingLiveStatus.textContent = st.error
        ? "Live transcription failed for the last window: " + st.error
        : st.skipped_s > 0
          ? `Model load ${Math.round(st.load * 100)}%. The live view skipped ${st.skipped_s} s of audio to keep up; the saved transcript is complete.`
          : `Model load ${Math.round(st.load * 100)}%.`;
    }
  } catch (err) {
    console.error("Live transcript failed:", err);
  } finally {
    meetingLiveBusy = false;
  }
}

function setMeetingLivePolling(on) {
  if (on && !meetingLiveTimer) {
    meetingLiveAfter = 0;
    meetingLiveGeneration += 1;
    meetingLiveBusy = false;
    meetingLiveText.textContent = "";
    pollMeetingLive();
    meetingLiveTimer = setInterval(pollMeetingLive, 2000);
  } else if (!on && meetingLiveTimer) {
    pollMeetingLive(); // the timer stops itself once the worker has ended (see pollMeetingLive)
  }
}

function renderMeetingState(status) {
  meetingRecording = !!status.recording;
  setMeetingLivePolling(meetingRecording);
  meetingElapsed = status.elapsed_s || 0;
  meetingIndicator.classList.toggle("recording", meetingRecording);
  meetingTimer.textContent = formatClock(meetingElapsed);
  meetingToggleBtn.textContent = meetingRecording ? "Stop recording" : "Start recording";
  if (meetingRecording && !meetingTick) {
    // The server's elapsed time is the truth; between polls the timer counts on its own.
    meetingTick = setInterval(() => {
      meetingElapsed += 1;
      meetingTimer.textContent = formatClock(meetingElapsed);
    }, 1000);
  } else if (!meetingRecording && meetingTick) {
    clearInterval(meetingTick);
    meetingTick = null;
  }
}

async function refreshMeetingStatus() {
  try {
    renderMeetingState(await fetch("/api/meetings/status").then((r) => r.json()));
  } catch (err) {
    console.error("Meeting status failed:", err);
  }
}

async function startMeeting() {
  meetingToggleBtn.disabled = true;
  showMeetingStatus("");
  try {
    const result = await fetch("/api/meetings/start", { method: "POST" }).then((r) => r.json());
    if (!result.ok) showMeetingStatus("✕ " + result.error, "down");
    else showMeetingStatus("● Recording. Dictation still works.", "ok");
    await refreshMeetingStatus();
  } finally {
    meetingToggleBtn.disabled = false;
  }
}

async function stopMeeting() {
  meetingToggleBtn.disabled = true;
  try {
    const result = await fetch("/api/meetings/stop", { method: "POST" }).then((r) => r.json());
    await refreshMeetingStatus();
    if (!result.ok && !result.busy) {
      showMeetingStatus("✕ " + result.error, "down");
    } else if (result.busy) {
      showMeetingStatus("● " + result.error + ". Transcribe it below once that is done.", "");
      loadFileTranscriptions();
    } else {
      trackMeetingJob(result.job_id);
    }
  } finally {
    meetingToggleBtn.disabled = false;
  }
}

// Stop starts the transcription job; its progress is shown here until the meeting appears in the list.
function trackMeetingJob(jobId) {
  clearInterval(meetingJobTimer);
  meetingProgress.style.display = "";
  const stop = () => {
    clearInterval(meetingJobTimer);
    meetingProgress.style.display = "none";
    loadFileTranscriptions();
  };
  const poll = async () => {
    let resp;
    try {
      resp = await fetch(`/api/file-transcribe/${jobId}`);
    } catch (err) {
      return; // no contact for a moment: retried on the next tick
    }
    if (!resp.ok) {
      // The job is gone (the app restarted, or it dropped out of the last ten): nothing says it succeeded.
      stop();
      showMeetingStatus("✕ Lost track of the transcription. See below whether the meeting was transcribed.", "down");
      return;
    }
    const job = await resp.json();
    meetingProgressBar.value = Math.round((job.percent || 0) * 100);
    if (job.state === "running") {
      showMeetingStatus(job.message || "Transcribing...", "");
      return;
    }
    stop();
    if (job.state === "done") showMeetingStatus("● Meeting transcribed.", "ok");
    else showMeetingStatus("✕ " + (job.error || "Transcription failed"), "down");
  };
  meetingJobTimer = setInterval(poll, 1000);
  poll();
}

// Recordings without a transcript: transcribe them or delete them (confirmation inside the card).
async function loadPendingMeetings() {
  const block = document.getElementById("meeting-pending");
  const list = document.getElementById("meeting-pending-list");
  const pending = await fetch("/api/meetings/pending").then((r) => r.json());
  block.hidden = !pending.length;
  list.innerHTML = pending.map((p) => `
    <div class="flagged-card" data-name="${escapeHtml(p.name)}">
      <div class="flagged-meta"><span>${p.started_at ? new Date(p.started_at).toLocaleString() : ""}</span></div>
      <div class="upload-filename">${escapeHtml(p.name)}</div>
      <div class="upload-delete-row">
        <button type="button" class="period-btn pending-transcribe">Transcribe</button>
        <button type="button" class="period-btn upload-delete-btn pending-delete">Delete</button>
      </div>
      <p class="status-line down pending-error" hidden></p>
    </div>`).join("");
  list.querySelectorAll(".flagged-card").forEach((card) => {
    const name = card.dataset.name;
    const row = card.querySelector(".upload-delete-row");
    const error = card.querySelector(".pending-error");
    const fail = (text) => { error.textContent = "✕ " + text; error.hidden = false; };
    card.querySelector(".pending-transcribe").addEventListener("click", async () => {
      error.hidden = true;
      const resp = await fetch(`/api/meetings/pending/${encodeURIComponent(name)}/transcribe`, { method: "POST" });
      const result = await resp.json();
      if (!resp.ok) return fail(result.error || "Could not start the transcription");
      trackMeetingJob(result.job_id);
      loadPendingMeetings();
    });
    card.querySelector(".pending-delete").addEventListener("click", () => {
      row.innerHTML = `
        <span class="upload-delete-ask">Delete this recording and its audio files? This cannot be undone.</span>
        <button type="button" class="period-btn upload-delete-btn pending-delete-confirm">Delete</button>
        <button type="button" class="period-btn pending-delete-cancel">Cancel</button>`;
      row.querySelector(".pending-delete-cancel").addEventListener("click", loadPendingMeetings);
      row.querySelector(".pending-delete-confirm").addEventListener("click", async () => {
        const resp = await fetch(`/api/meetings/pending/${encodeURIComponent(name)}`, { method: "DELETE" });
        if (resp.ok) return loadPendingMeetings();
        fail((await resp.json()).error || "Delete failed");
      });
    });
  });
}

meetingToggleBtn.addEventListener("click", () => {
  if (meetingRecording) stopMeeting();
  else if (meetingConsentHint) meetingConsent.hidden = false;
  else startMeeting();
});

document.getElementById("meeting-consent-cancel").addEventListener("click", () => {
  meetingConsent.hidden = true;
});

document.getElementById("meeting-consent-ok").addEventListener("click", async () => {
  meetingConsent.hidden = true;
  if (document.getElementById("meeting-consent-skip").checked) {
    const s = await postSetting("/api/settings/meeting-consent-hint", { enabled: false }).then((r) => r.json());
    meetingConsentHint = s.meeting_consent_hint;
    meetingConsentToggle.checked = meetingConsentHint;
    document.getElementById("meeting-consent-skip").checked = false;
  }
  startMeeting();
});

meetingConsentToggle.addEventListener("change", async () => {
  const s = await postSetting("/api/settings/meeting-consent-hint", { enabled: meetingConsentToggle.checked }).then((r) => r.json());
  meetingConsentHint = s.meeting_consent_hint;
});

meetingDictationToggle.addEventListener("change", async () => {
  const wanted = meetingDictationToggle.checked;
  try {
    const r = await postSetting("/api/settings/meeting-leave-dictation-out", { enabled: wanted });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    meetingDictationToggle.checked = (await r.json()).meeting_leave_dictation_out;
  } catch (err) {
    meetingDictationToggle.checked = !wanted; // not saved: show the setting that is still in force
  }
});

function renderMeetingHotkey(s) {
  const keycaps = document.getElementById("meeting-hotkey-keycaps");
  keycaps.replaceChildren();
  describeHotkey(s.meeting_modifiers, s.meeting_key).split("+").forEach((name, i) => {
    if (i) keycaps.append(Object.assign(document.createElement("span"), { className: "keycaps-plus", textContent: "+" }));
    keycaps.append(Object.assign(document.createElement("kbd"), { textContent: name }));
  });
  if (!meetingHotkeyKey.options.length) {
    for (const k of s.hotkey_key_options || []) meetingHotkeyKey.append(new Option(k.replace("_", " "), k));
  }
  for (const cb of meetingHotkeyMods) cb.checked = s.meeting_modifiers.includes(cb.value);
  meetingHotkeyKey.value = s.meeting_key;
}

async function saveMeetingHotkey() {
  const modifiers = meetingHotkeyMods.filter((cb) => cb.checked).map((cb) => cb.value);
  const s = await postSetting("/api/settings/meeting-hotkey", { modifiers, key: meetingHotkeyKey.value }).then((r) => r.json());
  renderMeetingHotkey(s);
  meetingHotkeyStatus.hidden = false;
  if (s.error) {
    meetingHotkeyStatus.className = "status-line down";
    meetingHotkeyStatus.textContent = "✕ " + s.error;
  } else {
    meetingHotkeyStatus.className = "status-line ok";
    meetingHotkeyStatus.textContent = "● Meeting hotkey is now " + describeHotkey(s.meeting_modifiers, s.meeting_key);
  }
}

for (const cb of meetingHotkeyMods) cb.addEventListener("change", saveMeetingHotkey);
meetingHotkeyKey.addEventListener("change", saveMeetingHotkey);

function fillDeviceSelect(select, names, chosen) {
  select.replaceChildren(new Option("System default", ""));
  const all = chosen && !names.includes(chosen) ? [...names, chosen] : names;
  for (const name of all) select.append(new Option(name, name));
  select.value = chosen || "";
}

async function saveMeetingDevices() {
  await postSetting("/api/settings/meeting-devices", { mic: meetingMicSelect.value, output: meetingOutputSelect.value });
  renderMeetingDevicesInfo();
}
meetingMicSelect.addEventListener("change", saveMeetingDevices);
meetingOutputSelect.addEventListener("change", saveMeetingDevices);

// A meeting toggle that saves on change and shows the stored value (or the one still in force on failure).
function bindMeetingToggle(toggle, url, key) {
  toggle.addEventListener("change", async () => {
    const wanted = toggle.checked;
    try {
      const r = await postSetting(url, { enabled: wanted });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toggle["checked"] = (await r.json())[key];
    } catch (err) {
      toggle.checked = !wanted; // not saved: show the setting that is still in force
    }
  });
}
bindMeetingToggle(meetingStoreAudioToggle, "/api/settings/meeting-store-audio", "meeting_store_audio");
bindMeetingToggle(meetingLiveToggle, "/api/settings/meeting-live-transcript", "meeting_live_transcript");

// The meeting settings sit in the Settings tab; the Meetings tab shows the devices as an info line.
async function loadMeetingSettings() {
  const [settings, devices] = await Promise.all([
    fetch("/api/settings").then((r) => r.json()),
    fetch("/api/meetings/devices").then((r) => r.json()),
  ]);
  meetingConsentHint = settings.meeting_consent_hint;
  meetingConsentToggle.checked = meetingConsentHint;
  meetingDictationToggle.checked = settings.meeting_leave_dictation_out;
  meetingLiveToggle.checked = settings.meeting_live_transcript;
  meetingStoreAudioToggle.checked = settings.meeting_store_audio;
  renderMeetingHotkey(settings);
  fillDeviceSelect(meetingMicSelect, devices.inputs, devices.mic_device);
  fillDeviceSelect(meetingOutputSelect, devices.outputs, devices.output_device);
  renderMeetingDevicesInfo();
}

function renderMeetingDevicesInfo() {
  document.getElementById("meeting-info-mic").textContent = meetingMicSelect.value || "System default";
  document.getElementById("meeting-info-output").textContent = meetingOutputSelect.value || "System default";
}

// "Meeting settings" link: open the Settings tab, scroll to the Meetings card and flash it.
document.getElementById("meeting-settings-link").addEventListener("click", (e) => {
  e.preventDefault();
  history.replaceState(null, "", "#settings");
  activateTab("settings");
  const card = document.getElementById("meeting-settings-card");
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.classList.remove("flash-highlight");
  void card.offsetWidth; // restart the animation when the link is clicked again
  card.classList.add("flash-highlight");
  setTimeout(() => card.classList.remove("flash-highlight"), 2000);
});

async function openMeetingsTab() {
  await loadMeetingSettings();
  await refreshMeetingStatus();
  loadFileTranscriptions();
  // A meeting started with the hotkey while this tab is open shows up through the poll.
  clearInterval(meetingPoll);
  meetingPoll = setInterval(() => {
    if (!document.hidden && document.getElementById("tab-meetings").classList.contains("active")) refreshMeetingStatus();
  }, 3000);
}

// ── Dev Mode Detection ───────────────────────────────────────────────
const DEV_MODE = new URLSearchParams(window.location.search).has("dev");

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  loadMeetingSettings();
  if (DEV_MODE) {
    document.querySelectorAll("[data-dev-only]").forEach((el) => (el.style.display = ""));
  }
  loadExtensions().then(restoreTabFromHash);
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
  const colors = [COLORS.green, COLORS.green, COLORS.accent, COLORS.orange, COLORS.orange, COLORS.red];

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
