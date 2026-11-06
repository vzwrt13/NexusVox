/* NexusVox Dashboard — Page start: experimental features and the DOMContentLoaded loaders. Loads last. */

// ── Experimental features ────────────────────────────────────────────
// Features that are not part of v1 are hidden in index.html (`data-feature`) and have no endpoint unless
// `[experimental]` in config.toml turns them on; the page shows what the server says is on.
let FEATURES = {};

// Resolves when the flags are known; started at page load next to the other loaders, awaited where one is needed.
let featuresReady = Promise.resolve();

async function loadFeatures() {
  try {
    const r = await fetch("/api/features");
    if (!r.ok) throw new Error("HTTP " + r.status);
    FEATURES = await r.json();
  } catch (err) {
    console.error("Could not load the experimental feature flags; experimental features stay hidden:", err);
    FEATURES = {};
  }
  document.querySelectorAll("[data-feature]").forEach((el) => {
    el.hidden = !FEATURES[el.dataset.feature];
  });
}

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  featuresReady = loadFeatures();
  loadSettings();
  loadMeetingSettings();
  Promise.all([featuresReady, loadExtensions()]).then(restoreTabFromHash);
});
