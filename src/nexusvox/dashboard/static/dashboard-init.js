/* NexusVox Dashboard — Page start: experimental features, tab wiring, DOMContentLoaded loaders. Loads last. */

// ── Experimental features ────────────────────────────────────────────
// Features that are not part of v1 are hidden in index.html (`data-feature`) and have no endpoint unless
// `[experimental]` in config.toml turns them on; the page shows what the server says is on.
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
// The tabs go live here, not in core: by now every file their handlers call has run.
document.querySelectorAll(".tab").forEach(bindTab);
window.addEventListener("hashchange", restoreTabFromHash);
// Back on the first page: see guardHistory. The marker entry steps forward at once, so the page stays put.
window.addEventListener("popstate", (e) => {
  if (e.state && e.state.nv === "start") history.forward();
});
for (const type of ["pointerdown", "keydown"]) document.addEventListener(type, guardHistory, { capture: true });

document.addEventListener("DOMContentLoaded", () => {
  featuresReady = loadFeatures();
  // Before the tab loaders: extensions and the hash live in core, so a tab file that failed cannot take them along.
  Promise.all([featuresReady, loadExtensions()]).then(restoreTabFromHash);
  settingsReady = loadSettings();
  loadMeetingSettings();
  openSetupOnFreshInstall();
});
