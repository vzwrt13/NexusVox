/* NexusVox Dashboard — Home tab: model status, the three starting points, newest recordings, this week, a tip */

// ── Tips ─────────────────────────────────────────────────────────────
// A fixed list; "Next tip" steps through it. The first one shown changes with the day.
const HOME_TIPS = [
  "Say \"new paragraph\" or \"comma\" while you dictate: spoken punctuation and formatting work in English and German.",
  "Add names and product terms under Settings > Vocabulary so they are spelled the way you write them.",
  "Select text, hold the dictation hotkey and tap the polish key to have it rewritten by voice.",
  "Record a meeting while you dictate: both keep working at the same time.",
  "Drop an audio file anywhere on the Recordings tab to transcribe it.",
  "Review lists dictations that may have been misheard, with the audio next to the text.",
  "If your hotkey clashes with another app, record a new one under Settings > Dictation.",
  "The dashboard works offline: your recordings and transcripts stay on this computer.",
];
let homeTipIndex = Math.floor(Date.now() / 86400000) % HOME_TIPS.length;

function showHomeTip() {
  document.getElementById("home-tip-text").textContent = HOME_TIPS[homeTipIndex];
}

function nextHomeTip() {
  homeTipIndex = (homeTipIndex + 1) % HOME_TIPS.length;
  showHomeTip();
}

// ── Status pill ──────────────────────────────────────────────────────
// "Model loaded" is what the switch status says when nothing is switching; then the model name, the device and
// whether anything leaves this computer (only a custom server can be somewhere else).
function describeHomeStatus(model, device, status) {
  const parts = [];
  let state = "ok";
  const phase = (status && status.status) || "idle";
  if (phase === "error") {
    state = "down";
    parts.push("The speech model did not load");
  } else if (["stopping", "starting", "waiting", "downloading", "loading"].includes(phase)) {
    state = "warn";
    parts.push("Loading the speech model");
  } else {
    parts.push("Model loaded");
  }
  if (model && model.name) parts.push(model.name);
  if (device) parts.push(device === "cuda" ? "GPU" : "CPU");
  parts.push(model && model.custom ? "uses your own server" : "runs offline");
  return { state, text: parts.join(" · ") };
}

async function loadHomeStatus() {
  const el = document.getElementById("home-status");
  try {
    const [models, current, device, status] = await Promise.all([
      fetch("/api/models").then((r) => r.json()),
      fetch("/api/models/current").then((r) => r.json()),
      fetch("/api/device").then((r) => r.json()),
      fetch("/api/models/status").then((r) => r.json()).catch(() => null),
    ]);
    const model = models.find((m) => m.id === current.model) || { name: current.model };
    const { state, text } = describeHomeStatus(model, device.resolved, status);
    setStatus(el, state, text);
  } catch (err) {
    console.error("Could not load the model status:", err);
    setStatus(el, "down", "Could not read the model status");
  }
}

// ── Dictate card ─────────────────────────────────────────────────────
function renderHomeHotkey(s) {
  const keycaps = document.getElementById("home-hotkey-keycaps");
  keycaps.replaceChildren();
  describeHotkey(s.hotkey_modifiers || [], s.hotkey_key || "").split("+").forEach((name, i) => {
    if (i) keycaps.append(Object.assign(document.createElement("span"), { className: "keycaps-plus", textContent: "+" }));
    keycaps.append(Object.assign(document.createElement("kbd"), { textContent: name }));
  });
  document.getElementById("home-dictate-text").textContent =
    s.hotkey_mode === "toggle"
      ? "Press your hotkey to start, press it again to stop."
      : "Hold your hotkey, speak, release to type.";
}

async function loadHomeHotkey() {
  try {
    renderHomeHotkey(await fetch("/api/settings").then((r) => r.json()));
  } catch (err) {
    console.error("Could not load the hotkey:", err);
  }
}

// ── Recent ───────────────────────────────────────────────────────────
function renderHomeRecent(rows) {
  const list = document.getElementById("home-recent");
  list.replaceChildren();
  document.getElementById("home-recent-empty").hidden = rows.length > 0;
  for (const item of rows) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "home-recent-row";
    btn.dataset.kind = item.kind;
    const title = document.createElement("span");
    title.className = "home-recent-title";
    title.textContent = recordingTitle(item) || "Untitled";
    const meta = document.createElement("span");
    meta.className = "home-recent-meta";
    meta.textContent = [recordingDate(item, true), formatRecordingLength(item.duration_ms)].filter(Boolean).join(" · ");
    btn.append(title, meta);
    btn.addEventListener("click", () => {
      goToTab("recordings");
      showRecording(item.id);
    });
    li.append(btn);
    list.append(li);
  }
}

async function loadHomeRecent() {
  try {
    renderHomeRecent(await fetch("/api/file-transcriptions?limit=3").then((r) => r.json()));
  } catch (err) {
    console.error("Could not load the newest recordings:", err);
  }
}

// ── This week ────────────────────────────────────────────────────────
function localIsoDate(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatSavedMinutes(minutes) {
  const m = Math.max(0, Math.round(minutes || 0));
  if (m < 60) return m + " min";
  return Math.floor(m / 60) + " h " + (m % 60) + " min";
}

function renderHomeWeek(overview) {
  document.getElementById("home-week-words").textContent = (overview.total_words || 0).toLocaleString();
  document.getElementById("home-week-saved").textContent = formatSavedMinutes(overview.time_saved_minutes);
}

async function loadHomeWeek() {
  // The seven local days up to today, not the Analytics date filter.
  const end = new Date();
  const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 6);
  const query = new URLSearchParams({ start: localIsoDate(start), end: localIsoDate(end) });
  try {
    renderHomeWeek(await fetch("/api/overview?" + query).then((r) => r.json()));
  } catch (err) {
    console.error("Could not load this week's numbers:", err);
  }
}

// ── Opening Home ─────────────────────────────────────────────────────
let homeLoaded = false;

function openHomeTab() {
  homeLoaded = true;
  showHomeTip();
  loadHomeStatus();
  loadHomeHotkey();
  loadHomeRecent();
  loadHomeWeek();
}

// ── Actions ──────────────────────────────────────────────────────────
function goToTab(name) {
  history.replaceState(null, "", "#" + name);
  return activateTab(name);
}

document.getElementById("home-record").addEventListener("click", () => {
  goToTab("recordings");
  // The click the Start recording button takes (it also asks for the consent hint); a running meeting is left alone.
  if (!meetingRecording) meetingToggleBtn.click();
});

document.getElementById("home-upload").addEventListener("click", () => {
  goToTab("recordings");
  openUploadDialog();
});

document.querySelectorAll("[data-home-go]").forEach((link) => {
  link.addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey) return; // the browser opens the href in a new tab
    e.preventDefault();
    goToTab(link.dataset.homeGo);
  });
});

document.getElementById("home-tip-next").addEventListener("click", nextHomeTip);
