/* NexusVox Dashboard — Pip's daily speech bubble on Home: which one to say, once a day, and that it can be dismissed */

// What Pip says is in dashboard-pip-texts.js (milestones, tips, statistic lines); this file only chooses. The rules:
//   - at most one bubble per calendar day (the user's local day), and a day that has one keeps it on every visit;
//   - a dismissed bubble stays gone until the next day, and afterwards Pip sits quietly, without a bubble;
//   - the order is a milestone not said before, else a tip about a free feature never used (and not said before),
//     else a statistic of this week; a day with nothing to say has no bubble;
//   - it works from counts only (the ones Home fetches), never from dictated or transcribed text.
//
// What was said and dismissed is kept in the browser's localStorage, like the other small page preferences. The
// choice is made by pickPipGreeting, a function of the facts, the saved state and the day, so it is easy to test.

const PIP_GREETING_KEY = "nexusvox-pip-greeting";

function emptyPipGreetingState() {
  return { day: "", id: "", text: "", dismissed: false, seen: [] };
}

// The saved state, or an empty one for anything missing or unreadable (a damaged value is never fatal).
function readPipGreetingState() {
  try {
    const value = JSON.parse(localStorage.getItem(PIP_GREETING_KEY));
    if (!value || typeof value !== "object") return emptyPipGreetingState();
    const text = (v) => (typeof v === "string" ? v : "");
    return {
      day: text(value.day),
      id: text(value.id),
      text: text(value.text),
      dismissed: value.dismissed === true,
      seen: Array.isArray(value.seen) ? value.seen.filter((v) => typeof v === "string") : [],
    };
  } catch (e) {
    return emptyPipGreetingState();
  }
}

function writePipGreetingState(state) {
  try {
    localStorage.setItem(PIP_GREETING_KEY, JSON.stringify(state));
  } catch (e) {
    /* private mode: the bubble may come back on the next visit, which is harmless */
  }
}

function pipCount(facts, key) {
  const n = Number(facts && facts[key]);
  return Number.isFinite(n) ? n : 0;
}

// A whole number the way the page shows numbers ("12,345").
function formatPipNumber(n) {
  return Math.round(n).toLocaleString("en-US");
}

function pipDayNumber(day) {
  return Math.floor(Date.parse(day + "T00:00:00Z") / 86400000) || 0;
}

// The statistic line of the day, or null for a week without dictation or without a measurable saving.
function pipStatText(facts, day) {
  const words = pipCount(facts, "week_words");
  const saved = pipCount(facts, "week_time_saved_minutes");
  if (words <= 0 || saved < 1) return null;
  const minutes = Math.round(saved);
  const savedText = minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
  const values = { words: formatPipNumber(words), saved: savedText, wpm: String(pipCount(facts, "typing_wpm") || 40) };
  const template = PIP_STATS[pipDayNumber(day) % PIP_STATS.length];
  // Every occurrence of a placeholder is filled, so a template may use one twice.
  return template.replace(/\{(words|saved|wpm)\}/g, (_, key) => values[key]);
}

// The milestones to treat as said when there is no saved state yet (the first run, for example after an update of an
// installation that has a history): of the goals already reached, only the highest of each counter stays to be said,
// so someone with 20,000 words is not told about their first dictation. A new user has reached nothing: no change.
function backfilledPipSeen(facts) {
  const reached = PIP_MILESTONES.filter((m) => pipCount(facts, m.counter) >= m.atLeast);
  const highest = new Map();
  for (const m of reached) {
    const best = highest.get(m.counter);
    if (!best || m.atLeast > best.atLeast) highest.set(m.counter, m);
  }
  return reached.filter((m) => highest.get(m.counter) !== m).map((m) => m.id);
}

// The kind of a bubble from its id: a milestone, a tip or the statistic. It decides the look (PIP_BUBBLE_STYLES).
function pipKindOf(id) {
  if (PIP_MILESTONES.some((m) => m.id === id)) return "milestone";
  if (PIP_TIPS.some((t) => t.id === id)) return "tip";
  return "stat";
}

// A greeting as the page uses it: the saved or chosen id and text, plus the kind and the bubble style that follow.
function pipGreetingOf(id, text, dismissed) {
  const kind = pipKindOf(id);
  return { id, text, dismissed, kind, style: PIP_BUBBLE_STYLES[kind] || "a" };
}

// What to say today: { id, text } or null. `seen` lists the ids of milestones and tips said before.
function pickPipContent(facts, seen, day) {
  const milestone = PIP_MILESTONES.find((m) => !seen.includes(m.id) && pipCount(facts, m.counter) >= m.atLeast);
  if (milestone) return { id: milestone.id, text: milestone.text };
  const tip = PIP_TIPS.find((t) => !seen.includes(t.id) && pipCount(facts, t.unused) === 0);
  if (tip) return { id: tip.id, text: tip.text };
  const stat = pipStatText(facts, day);
  return stat ? { id: "stat-week", text: stat } : null;
}

// The bubble for `day`: { greeting: { id, text, dismissed, kind, style } | null, state }, `state` being what to save. A day that
// already has a bubble keeps it (dismissed or not); otherwise one is chosen, and a milestone or tip is remembered
// as said. A day that has nothing to say saves nothing, so a milestone reached later that day can still be said.
function pickPipGreeting(facts, savedState, day) {
  const state = savedState && typeof savedState === "object" ? savedState : emptyPipGreetingState();
  if (state.day === day && state.id) {
    return { greeting: pipGreetingOf(state.id, state.text, state.dismissed === true), state };
  }
  const hasHistory = state.day !== "" || (Array.isArray(state.seen) && state.seen.length > 0);
  const seen = hasHistory ? state.seen : backfilledPipSeen(facts);
  const content = pickPipContent(facts, seen, day);
  if (!content) return { greeting: null, state };
  const kept = content.id === "stat-week" ? seen : [...seen, content.id];
  return {
    greeting: pipGreetingOf(content.id, content.text, false),
    state: { day, id: content.id, text: content.text, dismissed: false, seen: kept },
  };
}

// Today's bubble was dismissed: the state to save for it.
function dismissPipGreeting(state) {
  return { ...state, dismissed: true };
}

// ── On the page ──────────────────────────────────────────────────────

// The bubble, one component in two styles (`style`): "a" is the text with a small "×" in the card's corner, "c" adds a
// small label line above the text (`label`, from PIP_LABELS). The "×" is a button with an accessible name. Built from
// text nodes, never from markup; the style is a class on the content (`pip-bubble-style-a` / `-c`).
function buildPipBubble(text, onDismiss, { style = "a", label = "" } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "pip-bubble-body pip-bubble-style-" + (style === "c" ? "c" : "a");
  if (style === "c" && label) {
    const eyebrow = document.createElement("span");
    eyebrow.className = "pip-bubble-eyebrow";
    eyebrow.textContent = label;
    wrap.append(eyebrow);
  }
  const message = document.createElement("span");
  message.className = "pip-bubble-message";
  message.textContent = text;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "pip-bubble-close";
  close.textContent = "×";
  close.setAttribute("aria-label", "Dismiss");
  close.title = "Dismiss";
  close.addEventListener("click", onDismiss);
  wrap.append(message, close);
  return wrap;
}

// Mounts Pip in `host` (Home's) with today's bubble, or without one when there is nothing to say or it was
// dismissed. `facts` is null when they could not be read: Pip then sits quietly. Goes through mountPip, so the
// Show Pip setting still hides him and the bubble. Returns the greeting that is shown, or null.
function showPipGreeting(host, facts, day = localIsoDate(new Date())) {
  // Pip stands in Home's bottom-right corner, so his bubble sits on his left.
  const quiet = () => mountPip(host, { pose: "idle", side: "left" });
  // While Show Pip is off nothing is drawn, so nothing is said and nothing is used up: the bubble waits for him.
  if (!facts || !pipEnabled) {
    quiet();
    return null;
  }
  const { greeting, state } = pickPipGreeting(facts, readPipGreetingState(), day);
  if (!greeting) {
    quiet();
    return null;
  }
  writePipGreetingState(state);
  if (greeting.dismissed) {
    quiet();
    return null;
  }
  // The state is read again when the link is clicked: another window may have saved a newer one meanwhile, and only
  // the bubble that is still today's is marked dismissed.
  const dismiss = () => {
    const current = readPipGreetingState();
    if (current.day === state.day && current.id === state.id) writePipGreetingState(dismissPipGreeting(current));
    quiet();
  };
  const bubble = buildPipBubble(greeting.text, dismiss, { style: greeting.style, label: PIP_LABELS[greeting.kind] || "" });
  mountPip(host, { pose: "tilt", side: "left", bubble });
  return greeting;
}
