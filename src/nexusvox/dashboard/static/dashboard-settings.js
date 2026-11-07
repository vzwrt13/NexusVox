/* NexusVox Dashboard — Settings tab: hotkeys, toggles, device, OS and voice commands, submenu and search */

// ── Settings ─────────────────────────────────────────────────────────
const translateToggle = document.getElementById("translate-toggle");
const hotkeyModeSelect = document.getElementById("hotkey-mode-select");
const micGuardToggle = document.getElementById("mic-guard-toggle");
const storeAudioToggle = document.getElementById("store-audio-toggle");
const logFullTextToggle = document.getElementById("log-full-text-toggle");
const openLogFolderButton = document.getElementById("open-log-folder");
const logOpenError = document.getElementById("log-open-error");
const logDirLabel = document.getElementById("log-dir");
const feedbackTabButton = document.getElementById("feedback-tab-button");
const feedbackPanel = document.getElementById("tab-feedback");
let feedbackUrl = "";
const learnFromEditsToggle = document.getElementById("learn-from-edits-toggle");
const hotkeyModeHint = document.getElementById("hotkey-mode-hint");
const hotkeyMods = Array.from(document.querySelectorAll("#hotkey-mods input"));
const hotkeyKeySelect = document.getElementById("hotkey-key-select");
const pasteKeySelect = document.getElementById("paste-key-select");
const polishKeySelect = document.getElementById("polish-key-select");
const polishTerminalsToggle = document.getElementById("polish-terminals-toggle");
const llmModelSelect = document.getElementById("llm-model-select");
const meetingOwnLabelInput = document.getElementById("meeting-own-label-input");
let meetingOwnLabelSaved = "";
const feedbackModeSelect = document.getElementById("feedback-mode-select");
const cleanupLevelSelect = document.getElementById("cleanup-level-select");
const llmStatus = document.getElementById("llm-status");
const cleanupLlmHint = document.getElementById("cleanup-llm-hint");
const uploadLlmHint = document.getElementById("upload-llm-hint");

const hotkeyRecordBtn = document.getElementById("hotkey-record-btn");
const hotkeyStatus = document.getElementById("hotkey-status");
const hotkeyAltHint = document.getElementById("hotkey-alt-hint");
const hotkeyResetBtn = document.getElementById("hotkey-reset-btn");
const hotkeyKeycaps = document.getElementById("hotkey-keycaps");
const hotkeyBadge = document.getElementById("hotkey-badge");
const hotkeyDefaultName = document.getElementById("hotkey-default-name");
let hotkeyDefault = { modifiers: ["ctrl", "win"], key: "" };

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
  const toggle = mode === "toggle";
  const marker = (id, hold, tog) => {
    const el = document.getElementById(id);
    if (el) el.textContent = toggle ? tog : hold;
  };
  marker(
    "paste-key-help",
    "While holding the hotkey, press the paste key once (or keep it down). Release to paste the last text again.",
    "Press the hotkey + the paste key to stop and paste the last text again.",
  );
  marker(
    "polish-key-help",
    "Select text first. While holding the hotkey, press the polish key once (or keep it down), e.g. P, and say what to do with the selection. Release to rewrite the selected text.",
    "Select text first, then start and say what to do with it. Press the hotkey + the polish key, e.g. P, to stop and rewrite the selected text. Held when you start also counts.",
  );
}

// Draws a hotkey as keycaps (Ctrl + Win) into `el`: the Settings and Meetings editors and the Home card share it.
function renderKeycapsInto(el, modifiers, key) {
  el.replaceChildren();
  describeHotkey(modifiers, key).split("+").forEach((name, i) => {
    if (i) el.append(Object.assign(document.createElement("span"), { className: "keycaps-plus", textContent: "+" }));
    el.append(Object.assign(document.createElement("kbd"), { textContent: name }));
  });
}

function renderKeycaps(modifiers, key) {
  renderKeycapsInto(hotkeyKeycaps, modifiers, key);
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
  renderAltHint(hotkeyAltHint, s.hotkey_modifiers);
}

function showHotkeyStatus(text, cls) {
  hotkeyStatus.hidden = !text;
  setStatus(hotkeyStatus, cls, text);
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

translateToggle.addEventListener("change", async () => {
  await postSetting("/api/settings/translate", { enabled: translateToggle.checked });
  loadTranslatorStatus();
});

const translatorStatus = document.getElementById("translator-status");

// Probed live so the toggle never looks like it works while nothing is listening.
async function loadTranslatorStatus() {
  setStatus(translatorStatus, "", "Checking translator…");
  let s;
  try {
    s = await api("/api/settings/translator-status");
  } catch (err) {
    setStatus(translatorStatus, "down", "○ Could not check the translator (" + err.message + ")");
    return;
  }
  if (s.reachable) {
    setStatus(translatorStatus, "ok", "● Translator reachable at " + s.url);
  } else {
    setStatus(
      translatorStatus,
      "down",
      "○ No translator at " + s.url + (translateToggle.checked ? " — text is typed untranslated" : ""),
    );
  }
}
micGuardToggle.addEventListener("change", () => postSetting("/api/settings/mic-guard", { enabled: micGuardToggle.checked }));
storeAudioToggle.addEventListener("change", () => postSetting("/api/settings/store-audio", { enabled: storeAudioToggle.checked }));
logFullTextToggle.addEventListener("change", () => postSetting("/api/settings/log-full-text", { enabled: logFullTextToggle.checked }));
async function openLogFolder(errorLine) {
  errorLine.hidden = true;
  try {
    const r = await fetch("/api/log-folder/open", { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).error || "The log folder could not be opened.");
  } catch (e) {
    errorLine.textContent = e.message;
    errorLine.hidden = false;
  }
}
openLogFolderButton.addEventListener("click", () => openLogFolder(logOpenError));
learnFromEditsToggle.addEventListener("change", async () => {
  await postSetting("/api/settings/learn-from-edits", { enabled: learnFromEditsToggle.checked });
  loadLearnStatus();
});

const learnStatus = document.getElementById("learn-status");
let lastSuggestedCount = null;

// Dictations happen while the dashboard stays open: refresh the status line every few
// seconds, but only while its line is on screen (the Settings tab and the Vocabulary page are open, the page is visible).
setInterval(() => {
  if (!document.hidden && learnStatus.offsetParent !== null) loadLearnStatus();
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
  setStatus(learnStatus, s.last && s.last.outcome === "suggested" ? "ok" : "");
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
  refreshLlmHints();
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
  refreshLlmHints(); // the hint follows the chosen level at once
  const resp = await postSetting("/api/settings/cleanup-level", { level: cleanupLevelSelect.value });
  const s = await resp.json();
  if (s.error) alert(s.error);
  cleanupLevelSelect.value = s.cleanup_level; // the server's answer wins, and so does the hint
  refreshLlmHints();
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
  lastSettings = s;
  translateToggle.checked = s.translate_enabled;
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
  loadLlmModels();
  meetingOwnLabelInput.value = meetingOwnLabelSaved = s.meeting_own_voice_label;
  cleanupLevelSelect.value = s.cleanup_level;
  feedbackModeSelect.value = s.feedback_mode;
  loadLlmStatus();
  micGuardToggle.checked = s.mic_guard_enabled;
  storeAudioToggle.checked = s.store_audio;
  logFullTextToggle.checked = s.log_full_text;
  logDirLabel.textContent = s.log_dir;
  feedbackUrl = s.feedback_url || "";
  feedbackTabButton.hidden = !feedbackUrl;
  feedbackPanel.hidden = !feedbackUrl;
  applyFeedbackAvailability(s);
  learnFromEditsToggle.checked = s.learn_from_edits;
  loadLearnStatus();
  polishTerminalsToggle.checked = s.polish_in_terminals;
  loadTranslatorStatus();
  await Promise.all([loadDeviceSettings(), loadModelSwitcher(), loadOsCommands(), loadVoiceCommands(), loadDictionary(), loadDictionarySuggestions(), loadPolishPresets()]);
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
    setStatus(deviceResolved, "", "");
  }
}

function renderDeviceHint(d) {
  if (!deviceResolved) return;
  const resolvedLabel = d.resolved === "cuda" ? "GPU (CUDA)" : "CPU";
  const cudaNote = d.cuda_available ? "CUDA available on this system." : "No CUDA device detected.";
  deviceResolved.textContent = `Resolved: ${resolvedLabel} — ${cudaNote}`;
  // "Auto" that ended up on the CPU is a fallback (works, slower): yellow. A GPU asked for explicitly on a
  // machine without CUDA will not work after the restart: red.
  let state = "";
  if (d.requested === "auto" && d.resolved !== "cuda") state = "warn";
  else if (d.requested === "cuda" && !d.cuda_available) state = "down";
  setStatus(deviceResolved, state);
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
  await featuresReady;
  if (!FEATURES.os_commands) return; // experimental: the card is hidden and the endpoint is not registered
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

// -- Settings pages and search ------------------------------------------
// Each Settings card is one page. The navigation next to them lists the pages and shows one at a time (the first
// by default); while the search field holds text, every card with a match is listed instead, one under the other.
function settingsCards() {
  return Array.from(document.querySelectorAll("#settings-pages > .settings-card"));
}

function settingsLinks() {
  return Array.from(document.querySelectorAll("#settings-nav .settings-link"));
}

// Code in a later file follows the pages with onSettingsPageOpen(fn): fn(cardId) runs each time a page opens.
const settingsPageListeners = [];
function onSettingsPageOpen(fn) {
  settingsPageListeners.push(fn);
}

// Open the page of a card; a search filter is lifted first, so the page always shows in full.
function showSettingsPage(id) {
  if (document.getElementById("settings-search").value) clearSettingsSearch();
  for (const card of settingsCards()) card.classList.toggle("active", card.id === id);
  for (const link of settingsLinks()) {
    const on = link.dataset.card === id;
    link.classList.toggle("active", on);
    if (on) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  settingsPageListeners.forEach((fn) => fn(id));
  return document.getElementById(id);
}

// The open page is kept in the URL (#settings/models), so a reload, a new tab or a link lands on it.
function settingsPageHash() {
  const link = settingsLinks().find((l) => l.classList.contains("active"));
  return "#settings" + (link ? "/" + link.dataset.page : "");
}

function showSettingsPageBySlug(slug) {
  const link = settingsLinks().find((l) => l.dataset.page === slug);
  if (link) showSettingsPage(link.dataset.card);
}

// Open a page from another tab (the Meetings link on the Recordings tab) and bring it into view.
function openSettingsPageFromElsewhere(id) {
  const card = showSettingsPage(id);
  history.replaceState(null, "", settingsPageHash());
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  return card;
}

settingsLinks().forEach((link) => {
  link.addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey) return; // the browser opens the href (#settings/<page>) in a new tab
    e.preventDefault();
    showSettingsPage(link.dataset.card);
    history.replaceState(null, "", settingsPageHash());
    // Stacked below the sidebar tabs on a narrow window the page can be far below the fold: show its top.
    document.getElementById("settings-pages").scrollIntoView({ block: "start" });
  });
});

// Text a user can see and read as a label or hint: no option lists, buttons, fields, hidden parts or user data.
function searchableText(node) {
  if (node.nodeType === 3) return node.data;
  if (node.nodeType !== 1 || node.hidden) return "";
  if (["SELECT", "OPTION", "BUTTON", "INPUT", "TEXTAREA", "SCRIPT"].includes(node.tagName)) return "";
  if (node.classList.contains("dict-list")) return "";
  return Array.from(node.childNodes).map(searchableText).join(" ");
}

// A unit is a direct child of a card (setting row, sub-block, hint), apart from the title.
function settingsUnits(card) {
  return Array.from(card.children).filter((u) => !u.hidden && u.tagName !== "H4");
}

function applySettingsSearch() {
  const query = document.getElementById("settings-search").value.trim().toLowerCase();
  const tab = document.getElementById("tab-settings");
  let shown = 0;
  for (const card of settingsCards()) {
    const units = settingsUnits(card);
    card.classList.remove("search-hidden");
    units.forEach((u) => u.classList.remove("search-hidden"));
    if (!query) continue;
    const titleText = (card.querySelector(".nexus-section-title") || {}).textContent || "";
    if (titleText.toLowerCase().includes(query)) {
      shown++;
      continue;
    }
    let any = false;
    for (const u of units) {
      if (searchableText(u).toLowerCase().includes(query)) any = true;
      else u.classList.add("search-hidden");
    }
    if (any) shown++;
    else card.classList.add("search-hidden");
  }
  tab.classList.toggle("searching", !!query);
  for (const link of settingsLinks()) {
    const card = document.getElementById(link.dataset.card);
    link.classList.toggle("search-hidden", card.classList.contains("search-hidden"));
  }
  document.getElementById("settings-no-match").hidden = !query || shown > 0;
}

function clearSettingsSearch() {
  document.getElementById("settings-search").value = "";
  applySettingsSearch();
}

document.getElementById("settings-search").addEventListener("input", applySettingsSearch);
document.getElementById("settings-search").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.value) {
    e.preventDefault();
    clearSettingsSearch();
  }
});

// "Run the first-run setup again": the setup itself announces itself by enabling this button and listening for the event.
document.getElementById("settings-rerun-setup").addEventListener("click", () => {
  document.dispatchEvent(new CustomEvent("nexusvox:rerun-setup"));
});

// The learn-from-edits line is polled only while its page is on screen, and drawn at once when the page opens.
onSettingsPageOpen((id) => {
  if (id === "settings-card-vocabulary") loadLearnStatus();
});
