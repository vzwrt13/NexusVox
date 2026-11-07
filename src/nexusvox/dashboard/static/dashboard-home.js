/* NexusVox Dashboard — Home tab: model status, the three starting points, newest recordings, this week, a tip */

// ── Tips ─────────────────────────────────────────────────────────────
// A fixed list; "Next tip" steps through it. The first one shown changes with the local day.
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
let homeTipIndex = Math.floor((Date.now() - new Date().getTimezoneOffset() * 60000) / 86400000) % HOME_TIPS.length;

function showHomeTip() {
  document.getElementById("home-tip-text").textContent = HOME_TIPS[homeTipIndex];
}

function nextHomeTip() {
  homeTipIndex = (homeTipIndex + 1) % HOME_TIPS.length;
  showHomeTip();
}

// ── Status line ──────────────────────────────────────────────────────
// What the dashboard knows: the model, the device and whether anything leaves this computer (only a custom
// server can be somewhere else). Green is not claimed: nothing here probes the Docker container or the custom
// server, so a model that is not switching is shown plainly. Yellow while the model downloads or loads, red when
// that failed.
const HOME_BUSY_PHASES = ["stopping", "starting", "waiting", "downloading", "loading"];

function describeHomeStatus(model, device, status) {
  const phase = (status && status.status) || "idle";
  let state;
  let lead = "Speech model";
  if (phase === "error") {
    state = "down";
    lead = "The speech model did not load";
  } else if (HOME_BUSY_PHASES.includes(phase)) {
    state = "warn";
    lead = "Loading the speech model";
  }
  const parts = [lead];
  if (model && model.name) parts.push(model.name);
  if (device) parts.push(device === "cuda" ? "GPU" : "CPU");
  parts.push(model && model.custom ? "uses your own server" : "runs offline");
  return { state, text: parts.join(" · ") };
}

const homeStatusTimers = new Map(); // element -> timer of its next poll
let homeFirstOpen = true;

// While a model downloads or loads the line is asked again, until it settles or its page is left. The first-run
// setup shows the same line (`el`), and says whether it is still on screen (`shown`).
async function loadHomeStatus(
  settingsAlreadyKnown,
  el = document.getElementById("home-status"),
  shown = () => document.getElementById("tab-home").classList.contains("active"),
) {
  clearTimeout(homeStatusTimers.get(el));
  try {
    const [current, device, status] = await Promise.all([
      fetch("/api/models/current").then((r) => r.json()),
      fetch("/api/device").then((r) => r.json()),
      fetch("/api/models/status").then((r) => r.json()).catch(() => null),
    ]);
    // The model list is the Models page's; at page start it is loaded by the time settingsAlreadyKnown resolves.
    await settingsAlreadyKnown;
    const model = modelSpecs[current.model] || { name: current.model };
    const { state, text } = describeHomeStatus(model, device.resolved, status);
    setStatus(el, state, text);
    if (status && HOME_BUSY_PHASES.includes(status.status)) {
      homeStatusTimers.set(
        el,
        setTimeout(() => {
          if (shown()) loadHomeStatus(settingsAlreadyKnown, el, shown);
        }, 3000),
      );
    }
  } catch (err) {
    console.error("Could not load the model status:", err);
    setStatus(el, "down", "Could not read the model status");
  }
}

// ── Dictate card ─────────────────────────────────────────────────────
function renderHomeHotkey(s) {
  renderKeycapsInto(document.getElementById("home-hotkey-keycaps"), s.hotkey_modifiers || [], s.hotkey_key || "");
  document.getElementById("home-dictate-text").textContent =
    s.hotkey_mode === "toggle"
      ? "Press your hotkey to start, press it again to stop."
      : "Hold your hotkey, speak, release to type.";
}

// At page start the settings are the ones the Settings page just fetched; later visits ask again, as the hotkey
// may have been changed on the way.
async function loadHomeHotkey(settingsAlreadyKnown, atPageStart) {
  try {
    await settingsAlreadyKnown;
    const s = atPageStart && lastSettings ? lastSettings : await fetch("/api/settings").then((r) => r.json());
    renderHomeHotkey(s);
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
    // showRecording opens the Recordings tab on that recording; without it (deleted meanwhile) the tab opens plain.
    btn.addEventListener("click", async () => {
      if (!(await showRecording(item.id))) goToTab("recordings");
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
function openHomeTab() {
  showHomeTip();
  // The first visit at page start waits for the Settings page's own load instead of repeating its requests.
  const atPageStart = homeFirstOpen;
  const known = atPageStart ? settingsReady.catch(() => {}) : Promise.resolve();
  homeFirstOpen = false;
  loadHomeStatus(known);
  loadHomeHotkey(known, atPageStart);
  loadHomeRecent();
  loadHomeWeek();
}

// ── Actions ──────────────────────────────────────────────────────────
function goToTab(name) {
  history.replaceState(null, "", "#" + name);
  return activateTab(name);
}

document.getElementById("home-record").addEventListener("click", async () => {
  goToTab("recordings");
  // `meetingRecording` is stale until the tab has asked the app, so ask first: a meeting started by hotkey or
  // from the tray is left alone, and one that was stopped meanwhile can be started.
  await refreshMeetingStatus();
  // The click the Start recording button takes (it also asks for the consent hint).
  if (!meetingRecording) meetingToggleBtn.click();
});

document.getElementById("home-upload").addEventListener("click", () => {
  goToTab("recordings"); // opening the tab already refreshes what the dialog shows
  if (!uploadDialog.open) uploadDialog.showModal();
});

document.querySelectorAll("[data-home-go]").forEach((link) => {
  link.addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey) return; // the browser opens the href in a new tab
    e.preventDefault();
    goToTab(link.dataset.homeGo);
  });
});

document.getElementById("home-tip-next").addEventListener("click", nextHomeTip);
