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
  const template = PIP_STATS[pipDayNumber(day) % PIP_STATS.length];
  return template
    .replace("{words}", formatPipNumber(words))
    .replace("{saved}", savedText)
    .replace("{wpm}", String(pipCount(facts, "typing_wpm") || 40));
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

// The bubble for `day`: { greeting: { id, text, dismissed } | null, state }, `state` being what to save. A day that
// already has a bubble keeps it (dismissed or not); otherwise one is chosen, and a milestone or tip is remembered
// as said. A day that has nothing to say saves nothing, so a milestone reached later that day can still be said.
function pickPipGreeting(facts, savedState, day) {
  const state = savedState && typeof savedState === "object" ? savedState : emptyPipGreetingState();
  if (state.day === day && state.id) {
    return { greeting: { id: state.id, text: state.text, dismissed: state.dismissed === true }, state };
  }
  const seen = Array.isArray(state.seen) ? state.seen : [];
  const content = pickPipContent(facts, seen, day);
  if (!content) return { greeting: null, state };
  const kept = content.id === "stat-week" ? seen : [...seen, content.id];
  return {
    greeting: { id: content.id, text: content.text, dismissed: false },
    state: { day, id: content.id, text: content.text, dismissed: false, seen: kept },
  };
}

// Today's bubble was dismissed: the state to save for it.
function dismissPipGreeting(state) {
  return { ...state, dismissed: true };
}

// ── On the page ──────────────────────────────────────────────────────

// The bubble: its text and a small "Got it" button that dismisses it. Built from text nodes, never from markup.
function buildPipBubble(text, onDismiss) {
  const wrap = document.createElement("div");
  const message = document.createElement("span");
  message.textContent = text;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "link-btn pip-bubble-dismiss";
  button.textContent = "Got it";
  button.addEventListener("click", onDismiss);
  wrap.append(message, button);
  return wrap;
}

// Mounts Pip in `host` (Home's) with today's bubble, or without one when there is nothing to say or it was
// dismissed. `facts` is null when they could not be read: Pip then sits quietly. Goes through mountPip, so the
// Show Pip setting still hides him and the bubble. Returns the greeting that is shown, or null.
function showPipGreeting(host, facts, day = localIsoDate(new Date())) {
  const quiet = () => mountPip(host, { pose: "idle" });
  if (!facts) {
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
  const dismiss = () => {
    writePipGreetingState(dismissPipGreeting(state));
    quiet();
  };
  mountPip(host, { pose: "tilt", bubble: buildPipBubble(greeting.text, dismiss) });
  return greeting;
}
