/* NexusVox Dashboard — Shared core: access guard, status helper, theme, API helper, tab switching, extensions */

// ── Access denied ────────────────────────────────────────────────────
// The server refuses every request without the session cookie, which happens when NexusVox was restarted
// while this tab stayed open. Every fetch (extension panels included) goes through here, so one banner
// tells the user what to do instead of each panel failing on a reply it cannot parse.
(() => {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await nativeFetch(...args);
    if (res.status === 403 && res.headers.get("X-NexusVox-Denied") && !document.getElementById("denied-banner")) {
      const banner = document.createElement("div");
      banner.id = "denied-banner";
      banner.setAttribute("role", "alert");
      banner.style.cssText =
        "position:fixed;top:0;left:0;right:0;z-index:1000;padding:12px 16px;text-align:center;" +
        "background:#ff6b6b;color:#1a1a1a;font-weight:600;";
      banner.textContent =
        "This dashboard session has ended (NexusVox was restarted). Reopen the dashboard from the NexusVox tray icon.";
      document.body.prepend(banner);
    }
    return res;
  };
})();

// ── Status colour rule ───────────────────────────────────────────────
// One rule for every status line: "ok" (green) = works as expected, "warn" (yellow) = works, but with a caveat
// the user should know (slower on the CPU, a fallback is active), "down" (red) = does not work or is not
// reachable. Anything else is neutral. Set the state through this helper, never by writing class names.
const STATUS_STATES = ["ok", "warn", "down"];
function setStatus(el, state, text) {
  el.classList.add("status-line");
  el.classList.remove(...STATUS_STATES);
  if (STATUS_STATES.includes(state)) el.classList.add(state);
  if (text !== undefined) el.textContent = text;
}

// ── Alt hotkey hint ──────────────────────────────────────────────────
// Yellow ("works with a caveat"): Alt in a hotkey can open the menu bar of the focused app.
const ALT_HOTKEY_HINT = "Alt in a hotkey can open the menu bar in apps such as Notepad. Pasted text can then get lost.";
function renderAltHint(el, modifiers) {
  const hasAlt = (modifiers || []).includes("alt");
  setStatus(el, hasAlt ? "warn" : undefined, hasAlt ? ALT_HOTKEY_HINT : "");
  el.hidden = !hasAlt;
}
// ── end of Alt hotkey hint ───────────────────────────────────────────

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

// What `[experimental]` turns on, filled by loadFeatures in dashboard-init.js.
let FEATURES = {};

// Resolves when the flags are known; started at page load next to the other loaders, awaited where one is needed.
let featuresReady = Promise.resolve();

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
// Every switch opens the tab at the top; only a deliberate jump (`keepScroll`, e.g. the "Meeting settings"
// link) scrolls on its own afterwards. The Meetings and Upload tabs became the Recordings tab: their old
// names (bookmarks, the tray's link) open it.
const TAB_ALIASES = { meetings: "recordings", upload: "recordings" };

function activateTab(name, { keepScroll = false } = {}) {
  name = TAB_ALIASES[name] || name;
  const btn = document.querySelector(`.tab[data-tab="${name}"]`);
  const panel = document.getElementById("tab-" + name);
  if (!btn || !panel || btn.hidden) return false;

  document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach((s) => s.classList.remove("active"));
  btn.classList.add("active");
  panel.classList.add("active");
  if (!keepScroll) window.scrollTo(0, 0);

  if (name === "analytics") loadAnalytics();
  if (name === "edit") loadFlaggedTranscriptions();
  if (name === "recordings") openRecordingsTab();
  if (name === "settings") {
    loadMeetingSettings();
    markActiveSubtab();
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
  // Opens a stored upload or meeting in the Recordings tab on its Transcript tab; `options.mark` marks a passage
  // ({start, end} in seconds, or {text}). A summary links to its transcript this way. Resolves with false when
  // there is no such recording.
  showTranscript(id, options) {
    return showRecording(id, options);
  },
  // A tab of its own in the Recordings detail pane, next to Summary, Transcript and Speakers (docs/EXTENSIONS.md).
  registerRecordingTab(id, tab) {
    return addRecordingTab(id, tab);
  },
  // Draws an extension tab again for one recording (or for all), e.g. once a summary has been generated.
  refreshRecordingTab(id, recordingId) {
    refreshRecordingTabContent(id, recordingId);
  },
  // Switches the detail pane to a tab: "summary", "transcript", "speakers" or an extension tab's id.
  selectRecordingTab(id) {
    return chooseRecordingTab(id);
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
  // The Recordings list may have been drawn before the export targets were known.
  if ((extensionExports.length || extensionUploadActions.length) && document.getElementById("tab-recordings").classList.contains("active")) {
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
  if (!name) return;
  // A hash for a tab that is off (an experimental one, or an extension that is gone) must not stay in the URL.
  if (!activateTab(name)) history.replaceState(null, "", window.location.pathname + window.location.search);
  else if (TAB_ALIASES[name]) history.replaceState(null, "", "#" + TAB_ALIASES[name]);
}


// ── Shared helpers ───────────────────────────────────────────────────
function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  // innerHTML leaves quotes alone, and callers put the result in quoted attributes too.
  return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function formatDuration(ms) {
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(1) + "s";
}

function postSetting(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
