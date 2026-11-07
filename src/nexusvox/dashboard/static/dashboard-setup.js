/* NexusVox Dashboard — First-run setup: Welcome, Microphone, Hotkey, Ready. A full-page overlay over the dashboard. */

// It opens by itself while [general].setup_done is false (a config created from the template on a fresh install) and
// from Settings > "Run the first-run setup again" (the `nexusvox:rerun-setup` event). "Skip setup" and finishing both
// end it for good and land on Home. The microphone and the hotkey, its mode and the feedback are the Settings
// controls: the setup writes through the same code and endpoints, so Settings shows what was chosen here.

const setupEl = document.getElementById("setup");
const setupPanels = Array.from(setupEl.querySelectorAll(".setup-panel"));
const setupStepItems = Array.from(setupEl.querySelectorAll(".setup-steps li"));
const setupMicSelect = document.getElementById("setup-mic-select");
const setupMeterBar = document.getElementById("setup-meter-bar");
const setupMeter = document.getElementById("setup-meter");
const setupMicStatus = document.getElementById("setup-mic-status");
const setupHotkeyKeycaps = document.getElementById("setup-hotkey-keycaps");
const setupHotkeyStatus = document.getElementById("setup-hotkey-status");
const setupHotkeyAltHint = document.getElementById("setup-hotkey-alt-hint");
const setupTest = document.getElementById("setup-test");
const setupTestStatus = document.getElementById("setup-test-status");
const setupModelStatus = document.getElementById("setup-model-status");

const SETUP_LAST_STEP = setupPanels.length - 1;
const SETUP_METER_INTERVAL_MS = 120;
// A bar that stays this still for this long gets a hint: the microphone may be muted or the wrong one.
const SETUP_QUIET_LEVEL = 0.05;
const SETUP_QUIET_HINT_MS = 6000;
const SETUP_FEEDBACK_NAMES = { visual: "Visual", sound: "Audio", both: "Visual and audio" };

let setupStep = 0;
let setupMeterTimer = null;
let setupMeterRun = 0; // changes on every start and stop, so a reply from an earlier run is dropped
let setupQuietSince = 0;
let setupSettings = null; // the settings as the setup last saw them

function setupIsOpen() {
  return !setupEl.hidden;
}

// ── Microphone level ─────────────────────────────────────────────────
function showSetupLevel(level) {
  const percent = Math.round(Math.max(0, Math.min(1, level)) * 100);
  setupMeterBar.style.width = percent + "%";
  setupMeter.setAttribute("aria-valuenow", String(percent));
}

function describeSetupLevel(reading) {
  if (reading.error) {
    setStatus(setupMicStatus, "down", "✕ " + reading.error);
    return;
  }
  const now = Date.now();
  if (reading.level >= SETUP_QUIET_LEVEL) {
    setupQuietSince = now;
    setStatus(setupMicStatus, "ok", "● Your microphone works.");
  } else if (now - setupQuietSince > SETUP_QUIET_HINT_MS) {
    setStatus(
      setupMicStatus,
      "warn",
      "No sound yet. Check that the microphone is not muted, or choose another one.",
    );
  }
}

async function pollSetupMeter(run) {
  if (run !== setupMeterRun) return;
  let reading;
  try {
    reading = await fetch("/api/setup/mic-level").then((r) => r.json());
  } catch (err) {
    reading = { level: 0, error: "The microphone level could not be read." };
  }
  if (run !== setupMeterRun) return;
  showSetupLevel(reading.level || 0);
  describeSetupLevel(reading);
  setupMeterTimer = setTimeout(() => pollSetupMeter(run), SETUP_METER_INTERVAL_MS);
}

function startSetupMeter() {
  stopSetupMeter();
  setupQuietSince = Date.now();
  setStatus(setupMicStatus, "", "Say something to see the bar move.");
  const run = ++setupMeterRun;
  pollSetupMeter(run);
}

// The server closes the stream by itself a few seconds after the last poll; this closes it at once.
function stopSetupMeter() {
  setupMeterRun++;
  clearTimeout(setupMeterTimer);
  showSetupLevel(0);
}

// ── Hotkey, mode and feedback ────────────────────────────────────────
function showSetupHotkeyStatus(text, state) {
  setupHotkeyStatus.hidden = !text;
  setStatus(setupHotkeyStatus, state, text);
}

function renderSetupHotkey(s) {
  setupSettings = s;
  renderKeycapsInto(setupHotkeyKeycaps, s.hotkey_modifiers || [], s.hotkey_key || "");
  renderAltHint(setupHotkeyAltHint, s.hotkey_modifiers || []);
  for (const radio of setupEl.querySelectorAll('input[name="setup-mode"]')) radio.checked = radio.value === s.hotkey_mode;
  for (const radio of setupEl.querySelectorAll('input[name="setup-feedback"]')) {
    radio.checked = radio.value === s.feedback_mode;
  }
  const keys = describeHotkey(s.hotkey_modifiers || [], s.hotkey_key || "");
  document.getElementById("setup-test-hint").textContent =
    s.hotkey_mode === "toggle"
      ? 'Click into the box, press ' + keys + ', say "Hello world" and press it again.'
      : 'Click into the box, hold ' + keys + ', say "Hello world" and let go.';
}

async function loadSetupSettings() {
  try {
    renderSetupHotkey(await fetch("/api/settings").then((r) => r.json()));
  } catch (err) {
    console.error("The setup could not read the settings:", err);
    showSetupHotkeyStatus("✕ The settings could not be read.", "down");
  }
}

document.getElementById("setup-hotkey-record").addEventListener("click", (e) => {
  showSetupHotkeyStatus("", undefined);
  startHotkeyRecording({ button: e.currentTarget, report: showSetupHotkeyStatus, onSaved: renderSetupHotkey });
});

for (const radio of setupEl.querySelectorAll('input[name="setup-mode"]')) {
  radio.addEventListener("change", async () => {
    try {
      const s = await postSetting("/api/settings/hotkey-mode", { mode: radio.value }).then((r) => r.json());
      // The Settings page shows the same choice.
      hotkeyModeSelect.value = s.hotkey_mode;
      renderHotkeyHint(s.hotkey_mode, s.hotkey_modifiers, s.hotkey_key);
      renderSetupHotkey(s);
    } catch (err) {
      console.error("The hotkey mode could not be saved:", err);
      loadSetupSettings();
    }
  });
}

for (const radio of setupEl.querySelectorAll('input[name="setup-feedback"]')) {
  radio.addEventListener("change", async () => {
    try {
      const s = await postSetting("/api/settings/feedback-mode", { mode: radio.value }).then((r) => r.json());
      feedbackModeSelect.value = s.feedback_mode;
      renderSetupHotkey(s);
    } catch (err) {
      console.error("The feedback mode could not be saved:", err);
      loadSetupSettings();
    }
  });
}

// ── Test box ─────────────────────────────────────────────────────────
// Dictated text is pasted at the cursor, so with the box focused it lands here like in any other app. Any text counts
// (not only "Hello world"): a German speaker saying "Hallo Welt" has made it work just as well.
setupTest.addEventListener("input", () => {
  if (setupTest.value.trim()) setStatus(setupTestStatus, "ok", "● It works: your words landed here.");
  else setStatus(setupTestStatus, "", "Waiting for your words...");
});

function loadSetupModelStatus() {
  loadHomeStatus(settingsReady.catch(() => {}), setupModelStatus, () => setupIsOpen() && setupStep === 2);
}

// ── Ready ────────────────────────────────────────────────────────────
function renderSetupSummary() {
  const s = setupSettings || {};
  document.getElementById("setup-sum-mic").textContent = setupMicSelect.selectedOptions.length
    ? setupMicSelect.selectedOptions[0].textContent
    : "System default";
  renderKeycapsInto(document.getElementById("setup-sum-keycaps"), s.hotkey_modifiers || [], s.hotkey_key || "");
  document.getElementById("setup-sum-mode").textContent =
    s.hotkey_mode === "toggle" ? "Toggle: press once to start, again to stop" : "Hold: record while you hold the keys";
  document.getElementById("setup-sum-feedback").textContent = SETUP_FEEDBACK_NAMES[s.feedback_mode] || "";
}

// ── Steps ────────────────────────────────────────────────────────────
function showSetupStep(n) {
  setupStep = Math.max(0, Math.min(SETUP_LAST_STEP, n));
  setupPanels.forEach((panel, i) => {
    panel.hidden = i !== setupStep;
  });
  setupStepItems.forEach((item, i) => {
    item.classList.toggle("done", i < setupStep);
    if (i === setupStep) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  if (setupStep === 1) startSetupMeter();
  else stopSetupMeter();
  if (setupStep === 2) {
    loadSetupSettings();
    loadSetupModelStatus();
  }
  if (setupStep === SETUP_LAST_STEP) renderSetupSummary();
  setupEl.scrollTo(0, 0);
  const heading = setupPanels[setupStep].querySelector(".setup-heading");
  if (heading) heading.focus({ preventScroll: true });
}

function openSetup() {
  if (setupIsOpen()) return;
  setupEl.hidden = false;
  // Everything behind the overlay is out of reach of the keyboard and of screen readers while it is open.
  document.querySelector("header").inert = true;
  document.querySelector("main").inert = true;
  setupTest.value = "";
  setStatus(setupTestStatus, "", "Waiting for your words...");
  showSetupHotkeyStatus("", undefined);
  loadMicrophones();
  loadSetupSettings(); // the Ready step summarises them, whichever way it is reached
  showSetupStep(0);
}

function closeSetup() {
  stopSetupMeter();
  fetch("/api/setup/mic-level/stop", { method: "POST" }).catch(() => {});
  setupEl.hidden = true;
  document.querySelector("header").inert = false;
  document.querySelector("main").inert = false;
}

// Finishing and skipping both end the setup for good. A failed save must not trap the user in it: the setup then
// simply shows again at the next start.
async function endSetup() {
  try {
    await postSetting("/api/setup/complete", {});
  } catch (err) {
    console.error("The setup could not be marked as done:", err);
  }
  closeSetup();
  goToTab("home");
}

async function openSetupOnFreshInstall() {
  try {
    const state = await fetch("/api/setup").then((r) => r.json());
    if (state.needed) openSetup();
  } catch (err) {
    console.error("Could not tell whether the first-run setup is due:", err);
  }
}

setupEl.querySelectorAll("[data-setup-next]").forEach((btn) => btn.addEventListener("click", () => showSetupStep(setupStep + 1)));
setupEl.querySelectorAll("[data-setup-back]").forEach((btn) => btn.addEventListener("click", () => showSetupStep(setupStep - 1)));
document.getElementById("setup-finish").addEventListener("click", endSetup);
document.getElementById("setup-skip").addEventListener("click", endSetup);
setupMicSelect.addEventListener("change", async () => {
  await saveMicrophone(setupMicSelect);
  startSetupMeter(); // the stream follows the choice: the server reopens it on the new device
});

// Settings > "Run the first-run setup again" (dashboard-settings.js dispatches the event).
document.addEventListener("nexusvox:rerun-setup", openSetup);
