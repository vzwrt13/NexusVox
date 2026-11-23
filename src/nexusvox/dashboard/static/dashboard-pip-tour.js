/* NexusVox Dashboard — Pip's "what's new" popup on Home: when to show it, once per version, and how it steps */

// What Pip introduces is in dashboard-pip-whats-new.js (PIP_WHATS_NEW, per version); this file only decides and draws.
// The rules:
//   - the popup shows on the first Home visit after an update to a version that has entries, once per version: the
//     version counts as seen only when the popup is closed (the × or Done). "Show me" opens a page and puts the popup
//     away, but the tour goes on: the entry it stopped at is saved, and the next Home visit continues with the following
//     one (so does a reload in the middle of it);
//   - a "What's new" link in the sidebar's version line opens the current version's entries again at any time, also when
//     Show Pip is off (then as a plain card at the bottom right of the page, without the budgie);
//   - only a newer version shows it: going back to an older version (a rollback) shows nothing and keeps the newest
//     version saved, so neither the old nor the newer popup comes a second time;
//   - a first installation is not an update: with no saved version and no history (no dictation, meeting or upload yet)
//     the version is saved silently and nothing is shown;
//   - that day the popup replaces the daily bubble (dashboard-pip-greeting.js): Pip never talks twice at once. The
//     day's bubble is not used up, so the next day's greeting is chosen as usual;
//   - it works offline from the page's own files, and from counts and the app version only.
//
// What was shown is kept in the browser's localStorage, like the other small page preferences. The decision is made by
// pickPipTour, a function of the version, the facts, the saved state and the day, so it is easy to test.

const PIP_TOUR_KEY = "nexusvox-pip-whats-new";

// `version` is the newest version whose popup was closed (seen), `day` the day a popup was shown, and `pending` / `step`
// the tour that is under way: its version and the entry it continues with (`pending` is "" when none is).
function emptyPipTourState() {
  return { version: "", day: "", pending: "", step: 0 };
}

// The saved state, or an empty one for anything missing or unreadable (a damaged value is never fatal).
function readPipTourState() {
  try {
    const value = JSON.parse(localStorage.getItem(PIP_TOUR_KEY));
    if (!value || typeof value !== "object") return emptyPipTourState();
    const text = (v) => (typeof v === "string" ? v : "");
    const step = Number.isInteger(value.step) && value.step > 0 ? value.step : 0;
    return { version: text(value.version), day: text(value.day), pending: text(value.pending), step };
  } catch (e) {
    return emptyPipTourState();
  }
}

function writePipTourState(state) {
  try {
    localStorage.setItem(PIP_TOUR_KEY, JSON.stringify(state));
  } catch (e) {
    /* private mode: the popup may come back on the next visit, which is harmless */
  }
}

// "v1.2.0" and "1.2.0" are the same version.
function pipVersionKey(version) {
  return typeof version === "string" ? version.trim().replace(/^v/i, "") : "";
}

// The numbers of a version ("1.2.0" -> [1, 2, 0]; a suffix like "rc1" is ignored), or null when it has none.
function pipVersionParts(version) {
  const parts = pipVersionKey(version)
    .split(".")
    .map((part) => parseInt(part, 10));
  return parts.length && parts.every((n) => Number.isFinite(n)) ? parts : null;
}

// Is `current` newer than the version saved as seen? Versions that cannot be compared count as newer when they differ.
function isNewerPipVersion(current, saved) {
  if (!saved) return true;
  const a = pipVersionParts(current);
  const b = pipVersionParts(saved);
  if (!a || !b) return current !== saved;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const diff = (a[i] || 0) - (b[i] || 0);
    if (diff) return diff > 0;
  }
  return false;
}

// The usable steps of a version: entries with a title and a text; a target is kept only when it is a string.
function pipTourSteps(version, data = PIP_WHATS_NEW) {
  const list = data && Object.prototype.hasOwnProperty.call(data, version) ? data[version] : null;
  if (!Array.isArray(list)) return [];
  return list
    .filter((e) => e && typeof e.title === "string" && e.title && typeof e.text === "string" && e.text)
    .map((e) => ({ title: e.title, text: e.text, target: typeof e.target === "string" && e.target ? e.target : "" }));
}

// Has the installation ever been used? Counts only. Used to tell a first installation from an update.
function pipHasHistory(facts) {
  return pipCount(facts, "dictations") + pipCount(facts, "meetings") + pipCount(facts, "uploads") > 0;
}

// The state once the popup of `version` is closed: the version is seen and no tour is under way.
function pipTourSeen(state, version) {
  const seen = isNewerPipVersion(version, state.version) ? version : state.version;
  return { ...state, version: seen, pending: "", step: 0 };
}

// The state while the tour of `version` is at entry `step` (where a later Home visit continues).
function pipTourAt(state, version, step) {
  return { ...state, pending: version, step };
}

// The entry an unfinished tour of `version` continues with, or 0 for a tour that starts afresh.
function pipResumeStep(state, version, count) {
  return state.pending === version && state.step > 0 && state.step < count ? state.step : 0;
}

// What to show for `version` on `day`: { steps, start, state }, `state` being what to save (the same object when
// nothing changes) and `start` the entry to begin with. `steps` is empty when there is nothing to introduce. Without a
// version or without the facts nothing is decided, so a failed request never uses up a popup. A version stays due until
// its popup is closed: a tour left in the middle (Show me, a reload) is continued at the entry it stopped at.
function pickPipTour(version, facts, savedState, day, data = PIP_WHATS_NEW) {
  const state = savedState && typeof savedState === "object" ? savedState : emptyPipTourState();
  const current = pipVersionKey(version);
  if (!current || !facts) return { steps: [], start: 0, state };
  const resuming = state.pending === current;
  if (!resuming && !isNewerPipVersion(current, state.version)) return { steps: [], start: 0, state };
  const steps = pipTourSteps(current, data);
  const firstInstall = state.version === "" && !resuming && !pipHasHistory(facts);
  // No popup for this version: `day` is cleared, so a popup shown earlier today (for the previous version) does not
  // hide today's bubble now.
  if (firstInstall || steps.length === 0) {
    return { steps: [], start: 0, state: { ...pipTourSeen(state, current), day: "" } };
  }
  const start = pipResumeStep(state, current, steps.length);
  return { steps, start, state: { ...pipTourAt(state, current, start), day } };
}

// ── On the page ──────────────────────────────────────────────────────

// One step as a card for the bubble slot. The × closes the popup for good; "Show me" (only for a step with a target)
// opens that page of the dashboard and puts the popup away, the tour going on at the next entry (a target that is not
// open to this user, such as a tab behind a feature flag that is off, gets no "Show me" at all); the main button is
// "Next", or "Done" on the last step. Built from text nodes, never from markup.
function buildPipTourStep(steps, index, { onNext, onClose, onShow, canShow = () => true }) {
  const step = steps[index];
  const last = index === steps.length - 1;
  const make = (tag, className, text) => {
    const el = document.createElement(tag);
    el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const wrap = make("div", "pip-bubble-body pip-bubble-style-c pip-tour");
  const count = steps.length > 1 ? " · " + (index + 1) + " of " + steps.length : "";
  const title = make("strong", "pip-tour-title", step.title);
  const text = make("span", "pip-bubble-message", step.text);
  const actions = make("div", "pip-tour-actions");
  if (step.target && canShow(step.target)) {
    const show = make("button", "link-btn pip-tour-show", "Show me");
    show.type = "button";
    show.addEventListener("click", () => onShow(step.target));
    actions.append(show);
  }
  const next = make("button", "pip-tour-next", last ? "Done" : "Next");
  next.type = "button";
  next.addEventListener("click", last ? onClose : onNext);
  actions.append(next);
  const close = make("button", "pip-bubble-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  close.title = "Close";
  close.addEventListener("click", onClose);
  wrap.append(make("span", "pip-bubble-eyebrow", "What's new" + count), title, text, actions, close);
  return wrap;
}

// Pip stands in Home's bottom-right corner, so his bubble sits on his left.
function pipQuiet(host) {
  mountPip(host, { pose: "idle", side: "left" });
}

// Is the dashboard tab of `target` ("recordings", "settings/vocabulary") shown to this user? A tab behind a feature flag
// that is off, or a Feedback tab that is hidden, is not.
function pipTargetAvailable(target) {
  const btn = document.querySelector('.tab[data-tab="' + target.split("/")[0] + '"]');
  return !!btn && !btn.hidden;
}

// Opens `target` like a click on that tab does: the tab, its address and, for Settings, the page itself (goToTab only
// pushes the address; the Settings page is shown by showSettingsPageBySlug). Returns false when the tab is not open.
function openPipTarget(target) {
  if (!pipTargetAvailable(target)) return false;
  const [tab, page] = target.split("/");
  const opened = goToTab(target);
  if (opened && tab === "settings") showSettingsPageBySlug(page || defaultSettingsSlug());
  return opened;
}

// The host whose popup is on screen now. Opening Home again (the sidebar's Home button on Home, Back, an overlapping
// load) must not swap a popup that is half read for a quiet Pip: the tour is already saved.
let pipTourHost = null;

// Walks through `steps` of `version` from entry `start`. `present(bubble)` draws one card (null clears it) and `onEnd`
// runs when the popup goes away. The state is saved as the cards change: the entry on screen is where a later visit
// continues, "Done" and the × mark the version as seen, and "Show me" saves the next entry (on the last one it marks
// the version as seen) before it opens the page.
function walkPipTour(present, steps, version, start, open, canOpen, onEnd) {
  const save = (update) => writePipTourState(update(readPipTourState()));
  const draw = (index) => {
    save((state) => pipTourAt(state, version, index));
    present(
      buildPipTourStep(steps, index, {
        onNext: () => draw(index + 1),
        onClose: () => {
          save((state) => pipTourSeen(state, version));
          onEnd();
        },
        onShow: (target) => {
          if (index + 1 < steps.length) save((state) => pipTourAt(state, version, index + 1));
          else save((state) => pipTourSeen(state, version));
          onEnd();
          open(target);
        },
        canShow: canOpen,
      }),
    );
  };
  draw(start);
}

// Walks through `steps` in `host`: one mount per step, so the Show Pip setting still hides all of it.
function showPipTour(host, steps, open = openPipTarget, canOpen = pipTargetAvailable, version = "", start = 0) {
  pipTourHost = host;
  walkPipTour(
    (bubble) => mountPip(host, { pose: "tilt", side: "left", bubble }),
    steps,
    version,
    start,
    open,
    canOpen,
    () => {
      pipTourHost = null;
      pipQuiet(host);
    },
  );
}

// The plain card (no budgie) the "What's new" link opens while Show Pip is off.
let pipPlainPopup = null;

function closePipPlainTour() {
  if (pipPlainPopup) pipPlainPopup.remove();
  pipPlainPopup = null;
}

function showPipPlainTour(steps, open, canOpen, version, start) {
  closePipPlainTour();
  const box = document.createElement("div");
  box.className = "pip-bubble pip-tour-plain";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-label", "What's new");
  document.body.append(box);
  pipPlainPopup = box;
  walkPipTour((bubble) => box.replaceChildren(bubble), steps, version, start, open, canOpen, closePipPlainTour);
}

// The "What's new" link in the sidebar: the entries of `version` (the running app's), from the entry an unfinished tour
// left off at, else from the first. With Show Pip on they are Pip's popup on Home; with him off, a plain card wherever
// the user is. Returns false when the version has no entries.
function openPipWhatsNew(version, data = PIP_WHATS_NEW, open = openPipTarget, canOpen = pipTargetAvailable) {
  const current = pipVersionKey(version);
  const steps = pipTourSteps(current, data);
  if (!steps.length) return false;
  const start = pipResumeStep(readPipTourState(), current, steps.length);
  const host = pipEnabled ? document.getElementById("home-pip") : null;
  if (host) {
    goToTab("home");
    showPipTour(host, steps, open, canOpen, current, start);
  } else {
    showPipPlainTour(steps, open, canOpen, current, start);
  }
  return true;
}

// The sidebar link is there only for a version that has entries.
function updatePipWhatsNewLink(version, data = PIP_WHATS_NEW) {
  const link = document.getElementById("whats-new-link");
  if (!link) return;
  link.dataset.version = pipVersionKey(version);
  link.hidden = pipTourSteps(link.dataset.version, data).length === 0;
}

// Home's Pip: the what's-new popup on the first visit after an update, else the daily bubble. `version` is the app
// version ("" when unknown). Returns "tour", "greeting" or "quiet" for what Pip is doing. While Pip is switched off the
// popup is neither shown nor used up (it comes when he is switched on and Home is opened again).
function showPipHome(host, facts, version, day = localIsoDate(new Date()), data = PIP_WHATS_NEW, open, canOpen) {
  if (pipTourHost === host) return "tour"; // the popup is still being read: leave it as it is
  const saved = readPipTourState();
  const { steps, start, state } = pickPipTour(version, facts, saved, day, data);
  if (steps.length && pipEnabled) {
    writePipTourState(state);
    showPipTour(host, steps, open, canOpen, pipVersionKey(version), start);
    return "tour";
  }
  if (steps.length) {
    pipQuiet(host);
    return "quiet";
  }
  if (state !== saved) writePipTourState(state); // a first installation, or a version without entries
  // The popup was shown today: the day's bubble is not told as well, and is not used up either.
  if (state.day === day && state.version === pipVersionKey(version)) {
    pipQuiet(host);
    return "quiet";
  }
  return showPipGreeting(host, facts, day) ? "greeting" : "quiet";
}
