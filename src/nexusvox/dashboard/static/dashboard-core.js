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
      banner.className = "denied-banner";
      setStatus(
        banner,
        "down",
        "This dashboard session has ended (NexusVox was restarted). Reopen the dashboard from the NexusVox tray icon."
      );
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

// ── Notice under a control ───────────────────────────────────────────
// A refusal or failure shows as a status line under the control's row (never a browser dialog). One line per
// anchor; an empty text hides it. `state` follows the status colour rule.
const noticeLines = new WeakMap();
function showNotice(anchor, state, text) {
  const host = anchor.closest(".setting-row") || anchor;
  let line = noticeLines.get(host);
  if (!line) {
    if (!text) return;
    line = document.createElement("p");
    line.className = "settings-hint";
    line.setAttribute("role", "status");
    // Inside the row's text block, so the Settings search keeps the notice with its row.
    const info = host === anchor ? null : host.querySelector(".setting-info");
    if (info) info.append(line);
    else host.insertAdjacentElement("afterend", line);
    noticeLines.set(host, line);
  }
  setStatus(line, state, text || "");
  line.hidden = !text;
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

// The page start's loadSettings (it also loads the model list) and the settings it fetched, so Home does not ask
// for the same data a second time while the page starts.
let settingsReady = Promise.resolve();
let lastSettings = null;

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
  const res = await fetch(path);
  return res.json();
}

// The Analytics From/To filter goes only onto analytics requests; settings, LLM status, pulls and the like use `api`.
async function analyticsApi(path) {
  const dp = dateParams();
  const sep = path.includes("?") ? "&" : "?";
  return api(dp.toString() ? path + sep + dp.toString() : path);
}

// ── Tab Switching ────────────────────────────────────────────────────
// The active tab is mirrored into the URL hash (e.g. "#analytics") so a page
// refresh, bookmark, or back/forward navigation lands on the same tab.
// Every step the user takes (a tab, a Settings page, an Analytics view, a recording opened from elsewhere) adds
// one history entry through `pushPage`; the hashchange that Back and Forward fire opens the page again
// (`restoreTabFromHash`). A hash that only corrects itself (an alias, a missing recording) goes through `replaceHash`.
// Every switch opens the tab at the top; only a deliberate jump (`keepScroll`, e.g. the "Meeting settings"
// link) scrolls on its own afterwards. The Meetings and Upload tabs became the Recordings tab: their old
// names (bookmarks, the tray's link) open it.
const TAB_ALIASES = { meetings: "recordings", upload: "recordings" };

// The hash as the page it shows: no hash is Home, a bare "#settings" its first page.
function pageOfHash(hash) {
  if (!hash) return "#home";
  if (hash === "#settings") return "#settings/" + defaultSettingsSlug();
  if (hash === "#analytics/overview") return "#analytics";
  return hash;
}

function samePage(a, b) {
  return pageOfHash(a) === pageOfHash(b);
}

function replaceHash(hash) {
  history.replaceState(history.state, "", hash); // keeps the entry's marker, so a reload does not guard again
}

// Back on the first page would leave the dashboard. Once the user has acted on the page (an entry added without
// a gesture is skipped by the browser's Back button), the first entry becomes a marker and a copy of it takes its
// place: Back lands on the marker, and the popstate handler in dashboard-init.js steps forward again. A reload
// keeps the marker, so it is added once per tab.
// It waits until the page start has settled the hash (`markHistoryReady`): the marker and its copy must carry the
// URL the user sees, since a later `replaceHash` corrects only the entry it is on.
let historyGuarded = false;
let historyReady = false;
function markHistoryReady() {
  historyReady = true;
}

function guardHistory() {
  if (historyGuarded || !historyReady) return;
  historyGuarded = true;
  if (history.state && history.state.nv) return;
  history.replaceState({ nv: "start" }, "", window.location.href);
  history.pushState({ nv: "page" }, "", window.location.href);
}

function pushPage(hash) {
  if (samePage(window.location.hash, hash)) return;
  guardHistory();
  history.pushState({ nv: "page" }, "", hash);
}

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

  if (name !== "review") stopReviewAudio();
  if (name !== "settings") cancelHotkeyRecording(); // leaving Settings ends a recording; no key is swallowed after

  if (name === "home") openHomeTab();
  if (name === "analytics") openAnalyticsTab();
  if (name === "edit") loadFlaggedTranscriptions();
  if (name === "recordings") openRecordingsTab();
  if (name === "settings") {
    loadMeetingSettings();
    loadMicrophones();
  }
  if (name === "review") loadReviewTranscriptions();
  if (name === "dev") loadBenchmarks();
  if (name.startsWith("ext-")) openExtensionPanel(name.slice(4));
  return true;
}

function bindTab(btn) {
  btn.addEventListener("click", () => {
    const name = btn.dataset.tab;
    const hash = name === "analytics" ? analyticsViewHash() : name === "settings" ? settingsPageHash() : "#" + name;
    pushPage(hash);
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
      extensionPanels[p.id] = {
        title: p.title, script: p.script, recordingScript: p.recording_script || null, section, container: null, render: null,
      };
    });
  } catch (err) {
    console.error("Failed to load extensions:", err);
  }
  // The Recordings tab may have opened before the extensions were known: draw its list again with the export
  // targets and actions, and add the scripts that register its detail-pane tabs.
  if (document.getElementById("tab-recordings").classList.contains("active")) {
    if (extensionExports.length || extensionUploadActions.length) loadFileTranscriptions();
    loadRecordingTabScripts();
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

// Resolves when the page is settled: only a recording has to be looked up first.
function restoreTabFromHash() {
  const hash = window.location.hash;
  const [name, page] = hash.replace(/^#/, "").split("/"); // "#settings/models": tab and page
  // No hash, or one for a tab that is off, opens Home.
  if (!name) {
    activateTab("home");
    return Promise.resolve();
  }
  if (name === "recordings" && page) return restoreRecording(hash, page);
  // A hash for a tab that is off (an experimental one, or an extension that is gone) must not stay in the URL.
  if (!activateTab(name)) {
    replaceHash(window.location.pathname + window.location.search);
    activateTab("home");
  } else if (TAB_ALIASES[name]) replaceHash("#" + TAB_ALIASES[name]);
  else if (name === "settings") showSettingsPageBySlug(page || defaultSettingsSlug());
  return Promise.resolve();
}

// "#recordings/12": that recording, as opened from elsewhere. showRecording opens the tab itself, so the tab is not
// opened twice. A recording that is gone or an id that cannot be read leaves the plain tab; the lookup is async, so
// nothing is changed once the user has moved on to another entry.
async function restoreRecording(hash, page) {
  let id = null;
  try {
    id = decodeURIComponent(page);
  } catch (err) {
    console.warn("The address names no recording:", err);
  }
  const current = () => window.location.hash === hash;
  if (id !== null && (await showRecording(id, { restore: true, current }))) return;
  if (!current()) return;
  replaceHash("#recordings");
  activateTab("recordings");
}


// ── Shared helpers ───────────────────────────────────────────────────
function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  // innerHTML leaves quotes alone, and callers put the result in quoted attributes too.
  return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// The server stores `created_at` as naive UTC ("2026-11-06T14:30:00"); new Date() would read that as local time.
function parseServerTime(value) {
  const text = String(value);
  return new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(text) ? text : text + "Z");
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
