/* NexusVox Dashboard — Pip, the budgie mascot: one inline SVG component, its poses, a bubble slot and the Show Pip switch */

// Pip is calm and small. This file only draws him; where he appears and what his bubbles say is up to the tab that
// mounts him, and a tab never mounts him by hand-writing markup: it goes through mountPip, so the "Show Pip" setting
// (Settings > Dictation) hides every Pip and every bubble in one place.
//
//   mountPip(host, { pose: "idle", size: 80, bubble: "text or a Node" })
//
// Colours come from the tokens in style.css (--pip-*, --green, --accent, --accent-light, --text); the motion (a slow
// breathing bob, a blink every few seconds) is CSS and stops under prefers-reduced-motion.

const PIP_POSES = ["idle", "blink", "tilt", "sleeping"];
const PIP_MIN_SIZE = 64;
const PIP_MAX_SIZE = 96;
const PIP_DEFAULT_SIZE = 80;

// Is Pip shown? Set from the "show_pip" setting by loadSettings and by the Settings switch. Until the setting is
// known he is shown, which is the default; a page that loads with him off hides him as soon as the setting arrives.
let pipEnabled = true;

// Every host a Pip is mounted in, with how it was mounted, so a change of the setting redraws them all.
const pipMounts = new Map();

// The drawing is the budgie of the promo mascot (viewBox 256, face centre y = 122). `tilt` turns the head and opens
// the eyes wider, as when listening. Nothing here comes from the user, so it is safe as markup.
function pipEyeOpen(x, y, r) {
  return `<circle class="pip-ink" cx="${x}" cy="${y}" r="${r}"/><circle class="pip-face" cx="${x - r / 3}" cy="${y - r / 3}" r="${r / 3}"/>`;
}

function pipEyeClosed(x, y) {
  return `<path class="pip-lid" d="M${x - 10} ${y - 1} q10 10 20 0"/>`;
}

function pipEyes(pose) {
  const y = 112;
  const xs = [100, 156];
  if (pose === "sleeping" || pose === "blink") return xs.map((x) => pipEyeClosed(x, y)).join("");
  const open = xs.map((x) => pipEyeOpen(x, y, pose === "tilt" ? 11 : 9)).join("");
  if (pose === "tilt") return open;
  // idle: the open eyes and the closed ones are both drawn; the CSS blink swaps them for a moment.
  const closed = xs.map((x) => pipEyeClosed(x, y)).join("");
  return `<g class="pip-eyes-open">${open}</g><g class="pip-eyes-closed">${closed}</g>`;
}

function pipSleepMarks() {
  return '<path class="pip-zzz" d="M196 46 h22 l-22 26 h22"/><path class="pip-zzz pip-zzz-small" d="M226 14 h14 l-14 17 h14"/>';
}

function pipSvg(pose) {
  const tilt = pose === "tilt" ? ' transform="rotate(-9 128 240)"' : "";
  return (
    '<svg class="pip-art" viewBox="0 0 256 256" aria-hidden="true" focusable="false">' +
    `<g${tilt}>` +
    '<path class="pip-wing" d="M150 212 l44 34 q6 6 -3 6 l-58 -14Z"/>' +
    '<path class="pip-foot" d="M106 226 v16 m0 0 l-11 5 m11 -5 l11 5 m-11 -5 v7"/>' +
    '<path class="pip-foot" d="M150 226 v16 m0 0 l-11 5 m11 -5 l11 5 m-11 -5 v7"/>' +
    '<ellipse class="pip-wing" cx="46" cy="150" rx="24" ry="50" transform="rotate(10 46 150)"/>' +
    '<ellipse class="pip-wing" cx="210" cy="150" rx="24" ry="50" transform="rotate(-10 210 150)"/>' +
    '<path class="pip-body" d="M128 26 C190 26 216 86 216 142 C216 200 182 232 128 232 C74 232 40 200 40 142 C40 86 66 26 128 26Z"/>' +
    '<ellipse class="pip-face" cx="128" cy="122" rx="62" ry="50"/>' +
    '<path class="pip-mark" d="M106 48 q11 -8 22 0 q11 8 22 0"/>' +
    '<path class="pip-mark" d="M96 65 q16 -10 32 0 q16 10 32 0"/>' +
    pipEyes(pose) +
    '<path class="pip-beak" d="M118 124 h20 q0 18 -10 23 q-10 -5 -10 -23Z"/>' +
    '<ellipse class="pip-cere" cx="128" cy="120" rx="12" ry="7.5"/>' +
    '<circle class="pip-cheek" cx="90" cy="142" r="8.5"/><circle class="pip-cheek" cx="166" cy="142" r="8.5"/>' +
    "</g>" +
    (pose === "sleeping" ? pipSleepMarks() : "") +
    "</svg>"
  );
}

function pipClampSize(size) {
  const n = Number(size);
  if (!Number.isFinite(n)) return PIP_DEFAULT_SIZE;
  return Math.min(PIP_MAX_SIZE, Math.max(PIP_MIN_SIZE, Math.round(n)));
}

// Pip as an element, or null while he is switched off. `bubble` is the bubble slot: text (inserted as text) or a
// Node; empty means no bubble. The bubble is styled here, what it says is up to the caller.
function createPip(options = {}) {
  if (!pipEnabled) return null;
  const pose = PIP_POSES.includes(options.pose) ? options.pose : "idle";
  const size = pipClampSize(options.size);
  const root = document.createElement("div");
  root.className = "pip pip-" + pose;
  root.style.setProperty("--pip-size", size + "px");
  const figure = document.createElement("div");
  figure.className = "pip-figure";
  figure.innerHTML = pipSvg(pose);
  root.append(figure);
  const bubble = options.bubble;
  if (bubble !== undefined && bubble !== null && bubble !== "") {
    const slot = document.createElement("div");
    slot.className = "pip-bubble";
    slot.append(bubble instanceof Node ? bubble : document.createTextNode(String(bubble)));
    root.prepend(slot);
  }
  return root;
}

function drawPipMount(host, options) {
  host.replaceChildren();
  const pip = createPip(options);
  host.hidden = !pip;
  if (pip) host.append(pip);
}

// Puts Pip into `host` (replacing what is there). While Pip is off the host stays empty and hidden, and turning him
// on later fills it. Mounting the same host again redraws it with the new options. Returns the host.
function mountPip(host, options = {}) {
  pipMounts.set(host, options);
  drawPipMount(host, options);
  return host;
}

function unmountPip(host) {
  pipMounts.delete(host);
  host.replaceChildren();
}

// The Show Pip setting. Redraws every mounted Pip, so switching him off removes the figures and the bubbles now.
function setShowPip(on) {
  pipEnabled = on !== false;
  for (const [host, options] of pipMounts) {
    if (!host.isConnected) pipMounts.delete(host);
    else drawPipMount(host, options);
  }
}
