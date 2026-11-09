/* NexusVox Dashboard — Settings tab, Models group: language model, Ollama hints, transcription model */

// ── Ollama install hint (pure helpers, run under Node in tests) ──────
// Free 1.0 bundles no language model runtime: dictation works without one, Cleanup, Polish and upload cleanup
// need Ollama. `ollamaHint` turns /api/settings/llm-status into the hint every area shows; null = nothing to say.
const OLLAMA_DOWNLOAD_URL = "https://ollama.com/download";
// Ollama at its default address is a fixed entry. When nothing answers there the page does not call it broken (red):
// a first-time user has probably never heard of it, so it says what Ollama is and links the install page.
const OLLAMA_SITE_URL = "https://ollama.com";
const OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434";
const OLLAMA_NOT_INSTALLED =
  "Not installed yet. Ollama is a free app that runs AI language models on your own PC. Cleanup and Polish stay off until it is installed and running; everything else works without it.";
// Installed but stopped: neutral too, with a button that starts it (`onStart(button)` does the work).
function renderOllamaNotRunning(el, onStart) {
  setStatus(el, undefined, "Ollama is installed but not running. ");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn";
  button.textContent = "Start Ollama";
  button.onclick = () => onStart(button);
  el.append(button);
  el.hidden = false;
}
function isDefaultOllamaUrl(url) {
  return String(url || "").replace(/\/+$/, "") === OLLAMA_DEFAULT_URL;
}
// Writes the neutral "not installed yet" line (no colour state) plus the install link into `el`.
function renderOllamaNotInstalled(el) {
  setStatus(el, undefined, `${OLLAMA_NOT_INSTALLED} `);
  const a = document.createElement("a");
  a.href = OLLAMA_SITE_URL;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = "Install Ollama";
  el.append(a);
  el.hidden = false;
}
function isLocalLlmUrl(url) {
  try {
    return ["127.0.0.1", "localhost", "[::1]"].includes(new URL(url).hostname);
  } catch {
    return false;
  }
}
// `here`: the hint sits in the Models card, next to the download button.
function ollamaHint(s, { here = false } = {}) {
  if (!s.reachable) {
    if (s.protocol && s.protocol !== "ollama") {
      return { state: "down", text: `○ No model server at ${s.url} — Cleanup and Polish are off until it answers.`, link: false };
    }
    if (!isLocalLlmUrl(s.url)) {
      return {
        state: "down",
        text: `○ No Ollama server answers at ${s.url}. Check that it is running and that [llm].url in config.toml is right.${here ? " The saved model is kept." : ""}`,
        link: false,
      };
    }
    return {
      state: "down",
      text: `○ Ollama is not running at ${s.url}. Cleanup, Polish and upload cleanup need it; dictation works without.${here ? " The saved model is kept." : ""} Install and start Ollama: `,
      link: true,
    };
  }
  if (s.model_installed === false) {
    return {
      state: "down",
      text: `○ Ollama is running, but model ${s.model} is not installed. ${here ? "Download it with the button below." : "Open Settings > Models to download it."}`,
      link: false,
    };
  }
  return null;
}
function hideHint(el) {
  setStatus(el, undefined, "");
  el.hidden = true;
}
// Writes the hint through setStatus() and appends the download link; clears and hides the element when all is well.
function renderOllamaHint(el, s, visible = true, opts = {}) {
  const hint = visible ? ollamaHint(s, opts) : null;
  if (!hint) {
    hideHint(el);
    return;
  }
  setStatus(el, hint.state, hint.text);
  if (hint.link) {
    const a = document.createElement("a");
    a.href = OLLAMA_DOWNLOAD_URL;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "ollama.com/download";
    el.append(a);
  }
  el.hidden = false;
}
// The Models card's own line: it also knows whether the model list could be read (`data` = /api/settings/llm-models).
function renderModelsHint(el, s, data) {
  if (!data) return hideHint(el);
  if (data.reachable) return renderOllamaHint(el, s, true, { here: true });
  if (!s.reachable && s.protocol === "ollama" && isDefaultOllamaUrl(s.url)) {
    if (data.ollama && data.ollama.state === "stopped") return renderOllamaNotRunning(el, startOllama);
    return renderOllamaNotInstalled(el);
  }
  if (s.reachable) {
    // The server answers but the model list does not come back: not a missing install.
    const what = data.protocol === "ollama" ? "Ollama" : "The server";
    setStatus(el, "down", `○ ${what} answers at ${s.url}, but its models cannot be listed. The saved model is kept.`);
  } else {
    return renderOllamaHint(el, s, true, { here: true });
  }
  el.hidden = false;
}
// All areas at once from one llm-status reply. `ui` = the elements and whether each feature is wanted, so the red
// hint only appears where the user turned the feature on (dictation alone never looks broken).
function renderLlmHints(s, ui) {
  if (!s) return;
  if (!ui.polishOn) hideHint(ui.polish);
  else if (ollamaHint(s)) renderOllamaHint(ui.polish, s);
  else {
    setStatus(ui.polish, "ok", `● Model ready at ${s.url} (model ${s.model})`);
    ui.polish.hidden = false;
  }
  renderOllamaHint(ui.cleanup, s, ui.cleanupOn);
  renderOllamaHint(ui.upload, s, ui.uploadOn);
  renderModelsHint(ui.models, s, ui.modelsData);
}
// ── end of Ollama install hint ───────────────────────────────────────

let lastLlmStatus = null;
let lastLlmModels = null;
function refreshLlmHints() {
  renderLlmHints(lastLlmStatus, {
    polish: llmStatus,
    cleanup: cleanupLlmHint,
    upload: uploadLlmHint,
    models: llmModelHint,
    polishOn: polishKeySelect.value !== "",
    cleanupOn: cleanupLevelSelect.value !== "off",
    uploadOn: document.getElementById("upload-cleanup-toggle").checked,
    modelsData: lastLlmModels,
  });
}

async function loadLlmStatus() {
  lastLlmStatus = await api("/api/settings/llm-status");
  refreshLlmHints();
}

// The hints have their own poll, independent of the model list: that one is skipped when its payload is unchanged
// or its card is hidden. Runs while a tab with a hint is open and the page is visible.
setInterval(() => {
  if (document.hidden) return;
  const open = ["tab-settings", "tab-recordings"].some((id) => document.getElementById(id).classList.contains("active"));
  if (open) loadLlmStatus().catch(() => {});
}, 5000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadLlmStatus().catch(() => {});
});

// ── Language model dropdown ──────────────────────────────────────────
const llmModelHint = document.getElementById("llm-model-hint");
const llmSlowHint = document.getElementById("llm-slow-hint");
const llmPullBox = document.getElementById("llm-pull-status");
const llmPullText = document.getElementById("llm-pull-text");
const llmPullProgress = document.getElementById("llm-pull-progress");
const llmPullCancel = document.getElementById("llm-pull-cancel");
const llmPullSelected = document.getElementById("llm-pull-selected");
let llmSavedModel = "";
let llmPullTimer = null;

// ── LLM card refresh (pure helpers, run under Node in tests) ─────────
const LLM_REFRESH_MS = 5000;

// Where the model runs, from what Ollama reports for a loaded model (`/api/ps`); otherwise from the hardware.
// Figures Ollama under-reports (`plausible: false`) are never shown as real, and never claim "fully in VRAM".
function llmRunsOn(data) {
  const l = data.loaded;
  if (!l) return { text: data.device === "gpu" ? "Not loaded (will use the GPU if it fits)" : "Not loaded (no CUDA GPU, will use the CPU)", warn: false };
  if (!l.plausible) return { text: l.on_gpu ? "GPU" : "Loaded (placement unknown)", warn: false };
  if (l.ram_gb === 0) return { text: "GPU (fully in VRAM)", warn: false };
  if (l.vram_gb === 0) return { text: "CPU (in RAM)", warn: true };
  return { text: `GPU + CPU (${Math.round((100 * l.vram_gb) / (l.vram_gb + l.ram_gb))}% on the GPU)`, warn: true };
}

// Memory the model needs: Ollama's real split when it is loaded and plausible, else an estimate from the size on disk.
function llmMemory(data, m) {
  if (!data.reachable || data.protocol !== "ollama") return "—";
  const l = data.loaded;
  if (l && l.plausible) {
    const parts = [];
    if (l.vram_gb > 0) parts.push(`${l.vram_gb} GB VRAM`);
    if (l.ram_gb > 0) parts.push(`${l.ram_gb} GB RAM`);
    return parts.join(" + ") || "—";
  }
  if (!m || !m.size_gb) return "—";
  return `~${m.size_gb} GB ${data.device === "gpu" ? "VRAM" : "RAM"} (estimate)`;
}

function llmOptionLabel(m) {
  let label = m.name;
  if (m.recommended_here) label += " — recommended for this PC";
  if (m.installed === false && m.size_gb) label += ` — not installed, ${m.size_gb} GB`;
  else if (m.installed === false) label += " — not installed";
  return label;
}

// What the dropdown's options depend on: a change here (and only here) rebuilds the <select>.
function llmOptionsKey(data) {
  return JSON.stringify([data.reachable, data.protocol, data.selected, data.recommended, data.other, data.servers, data.ollama, data.url, data.roles]);
}

// A model of a saved server other than the one in use: its option value carries the server, `@@["url","model"]`.
const LLM_SERVER_PREFIX = "@@";
function llmServerValue(url, model) {
  return LLM_SERVER_PREFIX + JSON.stringify([url, model]);
}
// The reverse: [url, model], or null for the value of a model on the server in use.
function llmServerChoice(value) {
  if (!value.startsWith(LLM_SERVER_PREFIX)) return null;
  try {
    const [url, model] = JSON.parse(value.slice(LLM_SERVER_PREFIX.length));
    return [url, model];
  } catch {
    return null;
  }
}

// The dropdown as plain data: groups of options. `label: null` = options without a group, `down` = a server that does
// not answer (shown red, its entry cannot be picked). Every saved server except the one in use gets a group of its own;
// the one in use is the main list (recommended and installed models).
function llmOptionGroups(data) {
  const groups = [];
  if (!data.reachable) {
    // Server down (or no model list): show the saved value only, nothing else can break.
    groups.push({ label: null, options: [{ value: data.selected, label: data.selected }] });
  } else {
    if (data.recommended.length) {
      groups.push({
        label: "Recommended",
        options: data.recommended.map((m) => ({ value: m.name, label: llmOptionLabel(m), muted: !m.installed })),
      });
    }
    if (data.other.length) {
      groups.push({
        label: data.protocol === "ollama" ? "Other installed models" : "Models on this server",
        options: data.other.map((m) => ({ value: m.name, label: m.installed === false ? `${m.name} — not installed` : m.name })),
      });
    }
  }
  const ollama = data.ollama;
  if (ollama && !ollama.active) {
    // Not red when it does not answer: it is simply not installed yet.
    if (!ollama.reachable) {
      const stopped = ollama.state === "stopped";
      groups.push({
        label: stopped ? "Ollama — installed, not running" : "Ollama — not installed yet",
        options: [
          {
            value: "",
            label: stopped ? "Start Ollama below to use models on this PC" : "Install Ollama (ollama.com) to use models on this PC",
            disabled: true,
          },
        ],
      });
    } else {
      groups.push({
        label: "Ollama",
        options: ollama.models.length
          ? ollama.models.map((m) => ({ value: llmServerValue(ollama.url, m.name), label: m.name }))
          : [{ value: "", label: "No models installed yet", disabled: true }],
      });
    }
  }
  for (const server of data.servers || []) {
    if (server.active) continue;
    if (!server.reachable) {
      groups.push({
        label: `${server.name} — not answering`,
        down: true,
        options: [{ value: "", label: `No answer: ${server.error || "unknown error"}`, disabled: true, down: true }],
      });
    } else {
      groups.push({
        label: server.name,
        options: server.models.length
          ? server.models.map((m) => ({ value: llmServerValue(server.url, m.name), label: m.name }))
          : [{ value: "", label: "No models listed", disabled: true }],
      });
    }
  }
  return groups;
}

// Models per task (Advanced). The value of the saved fast model in its dropdown, "" when none is picked.
function llmFastValue(data) {
  const fast = data.roles && data.roles.fast;
  return fast ? llmServerValue(fast.url.replace(/\/+$/, ""), fast.model) : "";
}

// The fast model's dropdown: the main model's list, with every option naming its server (the fast model may sit on
// another server than the main one). A model that is not installed cannot be picked here; nothing downloads.
function llmFastOptionGroups(data) {
  const mainUrl = String(data.url || "").replace(/\/+$/, "");
  const mainName = data.roles.main_server; // named by the server, the same rule as every other server name
  const groups = llmOptionGroups(data).map((g) => {
    const own = g.options.some((o) => o.value && !llmServerChoice(o.value));
    return {
      ...g,
      label: own ? (g.label ? `${mainName} — ${g.label}` : mainName) : g.label,
      options: g.options.map((o) =>
        o.value && !llmServerChoice(o.value)
          ? { ...o, value: llmServerValue(mainUrl, o.value), disabled: Boolean(o.disabled || o.muted) }
          : o,
      ),
    };
  });
  const fast = data.roles && data.roles.fast;
  const value = llmFastValue(data);
  if (!fast) {
    groups.unshift({ label: null, options: [{ value: "", label: "Choose a fast model", disabled: true }] });
  } else if (!groups.some((g) => g.options.some((o) => o.value === value))) {
    // Its server does not list it now (down, or removed): keep showing the saved choice.
    groups.unshift({ label: fast.server, options: [{ value, label: fast.model }] });
  }
  return groups;
}

// What the main model is used for, for the overview: everything unless per-task models are on.
function llmMainUsedFor(data) {
  const roles = data.roles;
  if (!roles || !roles.enabled || !roles.fast) return "Cleanup, Polish, upload cleanup, extensions";
  const main = roles.tasks.filter((t) => t.role === "main").map((t) => t.label);
  return [...main, "extensions"].join(", ");
}

// Refreshes the card while it is on screen and the page is visible. `fetchData()` returns the models payload,
// `render(data, rebuildOptions)` draws it. Nothing is drawn when the payload did not change, and the options are
// rebuilt only when they changed (an open dropdown stays open); `force` (after a save or a pull) always draws.
function createLlmRefresher({ fetchData, render, isActive, schedule, cancel }) {
  let timer = null;
  let started = false;
  let lastData = null;
  let lastOptions = null;
  async function refresh(force = false) {
    let data;
    try {
      data = await fetchData();
    } catch {
      return false;
    }
    const snapshot = JSON.stringify(data);
    if (!force && snapshot === lastData) return false;
    const options = llmOptionsKey(data);
    render(data, force || options !== lastOptions);
    lastData = snapshot;
    lastOptions = options;
    return true;
  }
  function tick() {
    if (isActive()) refresh();
  }
  return {
    refresh,
    start() {
      started = true;
      if (timer === null) timer = schedule(tick, LLM_REFRESH_MS);
    },
    stop() {
      if (timer !== null) cancel(timer);
      timer = null;
    },
    get started() {
      return started;
    },
    get running() {
      return timer !== null;
    },
    // The page became visible again (or the card was opened): refresh once.
    wake() {
      tick();
    },
  };
}
// ── end of LLM card refresh ──────────────────────────────────────────

function renderLlmOverview(data, m) {
  const installed = !m || m.installed === null ? (data.reachable ? "Not in the server's list" : "Unknown (server not reachable)") : m.installed ? "Yes" : "No";
  const size = m && m.size_gb ? `${m.size_gb} GB` : "Unknown";
  const runs = llmRunsOn(data);
  const rows = [
    ["Model", escapeHtml(data.selected)],
    ["Size on disk", size],
    ["Installed", installed],
    ["Runs on", `<span class="llm-runs-on"></span>`],
    ["Memory", llmMemory(data, m)],
    ["Used for", escapeHtml(llmMainUsedFor(data))],
  ];
  document.getElementById("llm-overview-grid").innerHTML = rows
    .map(([label, value]) => `<div class="overview-row"><span class="overview-label">${label}</span><span class="overview-value">${value}</span></div>`)
    .join("");
  setStatus(document.querySelector("#llm-overview-grid .llm-runs-on"), runs.warn ? "warn" : undefined, runs.text);
}

// Fills a model dropdown with option groups (`llmOptionGroups`).
function fillLlmSelect(select, groups) {
  select.replaceChildren();
  for (const group of groups) {
    const parent = group.label === null ? select : document.createElement("optgroup");
    if (group.label !== null) {
      parent.label = group.label;
      if (group.down) setStatus(parent, "down"); // red through the one helper; the text stays
      parent.disabled = Boolean(group.down);
      select.append(parent);
    }
    for (const o of group.options) {
      const opt = new Option(o.label, o.value);
      if (o.muted) opt.style.color = "var(--text-muted, gray)";
      if (o.down) setStatus(opt, "down");
      opt.disabled = Boolean(o.disabled);
      parent.append(opt);
    }
  }
}

// ── Models per task: the Advanced section ──
const llmModelTitle = document.getElementById("llm-model-title");
const llmPerTaskToggle = document.getElementById("llm-per-task-toggle");
const llmRolesDetails = document.getElementById("llm-roles-details");
const llmFastSelect = document.getElementById("llm-fast-select");
const llmFastStatus = document.getElementById("llm-fast-status");
const llmTaskRoles = document.getElementById("llm-task-roles");
const llmRolesError = document.getElementById("llm-roles-error");

function renderLlmRoles(data, rebuildOptions) {
  const roles = data.roles;
  if (!roles) return;
  llmPerTaskToggle.checked = roles.enabled;
  llmRolesDetails.hidden = !roles.enabled;
  llmModelTitle.textContent = roles.enabled ? "Main model" : "Language model (LLM)";
  if (!roles.enabled) return;
  if (rebuildOptions) {
    fillLlmSelect(llmFastSelect, llmFastOptionGroups(data));
    llmFastSelect.value = llmFastValue(data);
  }
  if (!roles.fast) setStatus(llmFastStatus, "warn", "No fast model picked yet: the main model does its tasks.");
  else setStatus(llmFastStatus, undefined, `Fast model: ${roles.fast.model} on ${roles.fast.server}`);
  if (!rebuildOptions) return; // the task selects change only with the roles: an open one stays open
  llmTaskRoles.replaceChildren();
  for (const task of roles.tasks) {
    const row = document.createElement("div");
    row.className = "overview-row";
    const label = document.createElement("span");
    label.className = "overview-label";
    label.textContent = task.label;
    const select = document.createElement("select");
    select.className = "select-input";
    select.setAttribute("aria-label", `Model for ${task.label}`);
    select.append(new Option("Fast model", "fast"), new Option("Main model", "main"));
    select.value = task.role;
    select.addEventListener("change", () => saveLlmTaskRole(task.id, select.value));
    const value = document.createElement("span");
    value.className = "overview-value";
    value.append(select);
    row.append(label, value);
    llmTaskRoles.append(row);
  }
}

// Posts one change of the Advanced section and redraws the card. A failure is shown on a line of its own, outside
// the details (they may be hidden) and never touched by the redraw, until the next change.
async function saveLlmRoles(url, body) {
  llmRolesError.hidden = true;
  try {
    const resp = await postSetting(url, body);
    const data = await resp.json();
    if (data.error) {
      setStatus(llmRolesError, "down", data.error);
      llmRolesError.hidden = false;
    }
  } catch {
    setStatus(llmRolesError, "down", "The dashboard could not reach the app. Try again.");
    llmRolesError.hidden = false;
  }
  await loadLlmModels();
}

function saveLlmTaskRole(task, role) {
  return saveLlmRoles("/api/settings/llm-task-role", { task, role });
}

llmPerTaskToggle.addEventListener("change", () =>
  saveLlmRoles("/api/settings/llm-per-task", { enabled: llmPerTaskToggle.checked }),
);

llmFastSelect.addEventListener("change", () => {
  const choice = llmServerChoice(llmFastSelect.value);
  if (choice) saveLlmRoles("/api/settings/llm-select", { url: choice[0], model: choice[1], role: "fast" });
});
// ── end of the Advanced section ──

function renderLlmModels(data, rebuildOptions) {
  llmSavedModel = data.selected;
  lastLlmModels = data;
  refreshLlmHints();
  loadLlmStatus().catch(() => {}); // the hints follow the live status, fetched on its own
  renderLlmServers(data.servers || [], data.ollama || null);
  llmSlowHint.hidden = !data.slow_hint;
  renderLlmRoles(data, rebuildOptions);
  if (rebuildOptions) {
    fillLlmSelect(llmModelSelect, llmOptionGroups(data));
    llmModelSelect.value = data.selected;
  }
  if (!data.reachable) {
    llmPullSelected.hidden = true;
    renderLlmOverview(data, null);
    return;
  }
  const selectedModel = [...data.recommended, ...data.other].find((m) => m.name === data.selected) || null;
  renderLlmOverview(data, selectedModel);
  const selectedMissing = Boolean(selectedModel) && selectedModel.installed === false && data.protocol === "ollama";
  llmPullSelected.hidden = !selectedMissing || llmModelSelect.disabled;
  llmPullSelected.textContent = `Download ${data.selected}`;
  llmPullSelected.dataset.sizeGb = selectedModel && selectedModel.size_gb ? selectedModel.size_gb : "";
  llmModelSelect.dataset.notInstalled = JSON.stringify(
    Object.fromEntries(data.recommended.filter((m) => !m.installed).map((m) => [m.name, m.size_gb])),
  );
}

// ── Custom servers ───────────────────────────────────────────────────
const llmServersList = document.getElementById("llm-servers-list");
const llmServerUrl = document.getElementById("llm-server-url");
const llmServerKey = document.getElementById("llm-server-key");
const llmServerName = document.getElementById("llm-server-name");
const llmServerAdd = document.getElementById("llm-server-add");
const llmServerStatus = document.getElementById("llm-server-status");

// The saved servers with their state; Remove asks twice (no browser dialog): the first click arms it.
function renderLlmServers(servers, ollama) {
  llmServersList.replaceChildren();
  if (ollama) llmServersList.append(ollamaRow(ollama));
  for (const server of servers) {
    const row = document.createElement("div");
    row.className = "overview-row";
    const label = document.createElement("span");
    label.className = "overview-label";
    label.textContent = `${server.name} (${server.protocol === "ollama" ? "Ollama" : "OpenAI-compatible"})`;
    const value = document.createElement("span");
    value.className = "overview-value";
    const state = document.createElement("span");
    const count = server.models.length;
    if (server.reachable) setStatus(state, "ok", `● ${server.url} · ${count} model${count === 1 ? "" : "s"}`);
    else setStatus(state, "down", `○ ${server.url} · not answering: ${server.error || "unknown error"}`);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeLlmServer(server.url, remove));
    value.append(state, " ", remove);
    row.append(label, value);
    llmServersList.append(row);
  }
  llmServersList.style.display = servers.length || ollama ? "" : "none";
}

// The fixed Ollama row: no Remove, and never red - "not installed yet" explains itself and links the install page.
function ollamaRow(ollama) {
  const row = document.createElement("div");
  row.className = "overview-row";
  const label = document.createElement("span");
  label.className = "overview-label";
  label.textContent = "Ollama (this PC)";
  const value = document.createElement("span");
  value.className = "overview-value";
  const state = document.createElement("span");
  if (ollama.reachable) {
    const count = ollama.models.length;
    setStatus(state, "ok", `● ${ollama.url} · ${count} model${count === 1 ? "" : "s"}`);
  } else if (ollama.state === "stopped") {
    renderOllamaNotRunning(state, startOllama);
  } else {
    renderOllamaNotInstalled(state);
  }
  value.append(state);
  row.append(label, value);
  return row;
}

// Starts the installed Ollama, waits for it on the server side (about 15 s), then reloads the model list.
async function startOllama(button) {
  button.disabled = true;
  button.textContent = "Starting Ollama...";
  try {
    const resp = await postSetting("/api/settings/ollama-start", {});
    const data = await resp.json();
    if (data.error) setStatus(llmServerStatus, "down", data.error);
    else if (!data.running) setStatus(llmServerStatus, "warn", data.hint);
    else setStatus(llmServerStatus, "ok", "Ollama is running.");
  } catch {
    setStatus(llmServerStatus, "down", "The dashboard could not reach the app. Try again.");
  }
  await loadLlmModels();
  loadLlmStatus();
}

let removeArmed = null;
async function removeLlmServer(url, button) {
  if (removeArmed !== button) {
    removeArmed = button;
    button.textContent = "Click again to remove";
    setTimeout(() => {
      if (removeArmed === button) removeArmed = null;
      button.textContent = "Remove";
    }, 4000);
    return;
  }
  removeArmed = null;
  const resp = await fetch("/api/settings/llm-servers", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await resp.json();
  if (data.error) setStatus(llmServerStatus, "down", data.error);
  await loadLlmModels();
}

llmServerAdd.addEventListener("click", async () => {
  const url = llmServerUrl.value.trim();
  if (!url) {
    setStatus(llmServerStatus, "down", "Enter the server address with its port, e.g. http://127.0.0.1:8081");
    return;
  }
  llmServerAdd.disabled = true;
  setStatus(llmServerStatus, undefined, "Looking for a model server...");
  try {
    const resp = await postSetting("/api/settings/llm-servers", {
      url,
      api_key: llmServerKey.value,
      name: llmServerName.value,
    });
    const data = await resp.json();
    if (data.error) {
      setStatus(llmServerStatus, "down", data.error);
      return;
    }
    const kind = data.server.protocol === "ollama" ? "Ollama" : "OpenAI-compatible";
    setStatus(llmServerStatus, "ok", `Saved ${data.server.name} (${kind}). Its models are in the list above.`);
    llmServerUrl.value = llmServerKey.value = llmServerName.value = "";
    await loadLlmModels();
  } catch {
    setStatus(llmServerStatus, "down", "The dashboard could not reach the app. Try again.");
  } finally {
    llmServerAdd.disabled = false;
  }
});
// ── end of custom servers ────────────────────────────────────────────

const llmRefresher = createLlmRefresher({
  fetchData: () => api("/api/settings/llm-models"),
  render: renderLlmModels,
  // On screen: the Settings tab is open, the page is visible and the dropdown is laid out (its Models page is open).
  isActive: () =>
    !document.hidden && document.getElementById("tab-settings").classList.contains("active") && llmModelSelect.offsetParent !== null,
  schedule: (fn, ms) => setInterval(fn, ms),
  cancel: (t) => clearInterval(t),
});

// The Models page opens: refresh at once, the list may have changed while it was out of sight.
onSettingsPageOpen((id) => {
  if (id === "settings-card-models") llmRefresher.wake();
});

// Draws the card now, whatever changed (after a save, a pull, or when the tab opens).
async function loadLlmModels() {
  await llmRefresher.refresh(true);
  llmRefresher.start();
}

document.addEventListener("visibilitychange", () => {
  // No polling while the page is hidden; one refresh as soon as it is visible again.
  if (document.hidden) {
    llmRefresher.stop();
  } else if (llmRefresher.started) {
    llmRefresher.start();
    llmRefresher.wake();
  }
});

async function saveLlmModel(name) {
  const resp = await postSetting("/api/settings/llm-model", { model: name });
  const s = await resp.json();
  if (s.error) alert(s.error);
  llmSavedModel = s.llm_model;
  await loadLlmModels();
  loadLlmStatus();
}

function showLlmPull(text, pct, busy) {
  llmPullBox.style.display = "flex";
  llmPullText.textContent = text;
  llmPullProgress.hidden = pct === null;
  if (pct !== null) llmPullProgress.value = pct;
  llmPullCancel.hidden = !busy;
}

function pollLlmPull() {
  clearInterval(llmPullTimer);
  llmPullTimer = setInterval(async () => {
    let p;
    try {
      p = await api("/api/settings/llm-pull");
    } catch {
      return;
    }
    if (p.status === "pulling") {
      const pct = p.total ? Math.round((p.completed / p.total) * 100) : null;
      showLlmPull(`Downloading ${p.model}: ${p.detail}${pct === null ? "" : ` ${pct}%`}`, pct, true);
      return;
    }
    clearInterval(llmPullTimer);
    llmModelSelect.disabled = false;
    if (p.status === "done") {
      showLlmPull(`${p.model} downloaded.`, null, false);
      await saveLlmModel(p.model); // selected only now that the pull succeeded
    } else if (p.status === "cancelled") {
      showLlmPull("Download cancelled.", null, false);
      await loadLlmModels();
    } else if (p.status === "error") {
      showLlmPull(`Download failed: ${p.error || "unknown error"}`, null, false);
      await loadLlmModels();
    }
    setTimeout(() => {
      if (!llmPullTimer || p.status !== "pulling") llmPullBox.style.display = "none";
    }, 5000);
  }, 1000);
}

// Asks, then downloads `name` through Ollama; the model is selected only after the pull succeeded.
async function startLlmPull(name, sizeGb) {
  const size = sizeGb ? ` (${sizeGb} GB)` : "";
  if (!confirm(`${name} is not installed. Download it now${size} through Ollama?`)) return;
  const resp = await postSetting("/api/settings/llm-pull", { model: name });
  const p = await resp.json();
  if (p.error && p.status !== "pulling") {
    alert(p.error);
    return;
  }
  llmModelSelect.disabled = true;
  llmPullSelected.hidden = true;
  showLlmPull(`Downloading ${name}...`, null, true);
  pollLlmPull();
}

// A model of another saved server: that server and the model become the one in use.
async function selectServerModel(url, model) {
  const resp = await postSetting("/api/settings/llm-select", { url, model });
  const s = await resp.json();
  if (s.error) setStatus(llmServerStatus, "down", s.error);
  await loadLlmModels();
  loadLlmStatus();
}

llmModelSelect.addEventListener("change", async () => {
  const name = llmModelSelect.value;
  const choice = llmServerChoice(name);
  if (choice) {
    llmModelSelect.value = llmSavedModel; // until the switch is saved
    await selectServerModel(choice[0], choice[1]);
    return;
  }
  const missing = JSON.parse(llmModelSelect.dataset.notInstalled || "{}");
  if (!(name in missing)) {
    await saveLlmModel(name);
    return;
  }
  llmModelSelect.value = llmSavedModel; // the old model stays selected until the pull succeeded
  await startLlmPull(name, missing[name]);
});
// The saved model itself can be missing (a fresh Ollama): selecting it again fires no change, so it has a button.
llmPullSelected.addEventListener("click", () => startLlmPull(llmSavedModel, Number(llmPullSelected.dataset.sizeGb) || null));
llmPullCancel.addEventListener("click", () => fetch("/api/settings/llm-pull", { method: "DELETE" }));

// ── Model Switcher ──────────────────────────────────────────────────
const modelSelect = document.getElementById("model-select");

// " ✓ downloaded" or " · not downloaded (~670 MB)" for a model with a copy on this PC; nothing for the
// custom server, or when the cache could not be read.
function modelDownloadMark(m) {
  if (m.downloaded === true) return " ✓ downloaded";
  if (m.downloaded === false) return ` · not downloaded${m.size ? ` (${m.size})` : ""}`;
  return "";
}

async function loadModelSwitcher() {
  const [models, current] = await Promise.all([
    fetch("/api/models").then((r) => r.json()),
    fetch("/api/models/current").then((r) => r.json()),
  ]);
  modelSpecs = Object.fromEntries(models.map((m) => [m.id, m]));
  modelSelect.innerHTML = models.map((m) =>
    `<option value="${m.id}"${m.id === current.model ? " selected" : ""}>${m.name}${modelDownloadMark(m)}</option>`
  ).join("");
  renderModelOverview(current.model);
  // An in-process model (Parakeet on CPU) may still be downloading or loading from the start.
  const status = await fetch("/api/models/status").then((r) => r.json()).catch(() => null);
  if (status && (status.status === "downloading" || status.status === "loading")) {
    showModelStatus(status.note ? `${status.note}. ${STATUS_MESSAGES[status.status]}` : STATUS_MESSAGES[status.status]);
    pollModelStatus(current.model);
  }
}

function renderModelOverview(modelId) {
  const m = modelSpecs[modelId];
  if (!m) return;
  const protocolLabel = m.protocol === "realtime_ws" ? "WebSocket (Realtime)" : "HTTP (OpenAI-compatible)";
  let runtimeLabel;
  if (m.custom) {
    runtimeLabel = "External server (no Docker)";
  } else if (m.inprocess_supported) {
    runtimeLabel = "CPU in-process · GPU via Docker";
  } else {
    runtimeLabel = "GPU (Docker)";
  }
  const rows = [
    ["Description", escapeHtml(m.description)],
    ["Architecture", escapeHtml(m.architecture)],
    ["Parameters", escapeHtml(m.parameters)],
    ["Protocol", protocolLabel],
    ["Runtime", runtimeLabel],
    ["Streaming", m.streaming ? "Yes" : "No"],
    ["Languages", escapeHtml(m.languages)],
    ["VRAM (approx.)", escapeHtml(m.vram_gb)],
    m.custom
      ? ["Server", escapeHtml(m.server_url || "Not set: add [inference.custom] url to config.toml")]
      : ["HuggingFace", `<span class="overview-hf-link" title="${escapeHtml(m.hf_name)}">${escapeHtml(m.hf_name)}</span>`],
  ];
  document.getElementById("model-overview-grid").innerHTML = rows.map(([label, value]) =>
    `<div class="overview-row"><span class="overview-label">${label}</span><span class="overview-value">${value}</span></div>`
  ).join("");
}

modelSelect.addEventListener("change", async () => {
  renderModelOverview(modelSelect.value);
  const prev = modelSelect.dataset.prev || modelSelect.value;
  modelSelect.disabled = true;
  showModelStatus("Initiating switch...");

  try {
    const res = await fetch("/api/models/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelSelect.value }),
    });
    const data = await res.json();
    if (!data.ok) {
      modelSelect.value = prev;
      hideModelStatus();
      modelSelect.disabled = false;
      return;
    }
    pollModelStatus(prev);
  } catch {
    modelSelect.value = prev;
    hideModelStatus();
    modelSelect.disabled = false;
  }
});

function showModelStatus(text) {
  const el = document.getElementById("model-switch-status");
  document.getElementById("model-status-text").textContent = text;
  el.style.display = "flex";
}

function hideModelStatus() {
  document.getElementById("model-switch-status").style.display = "none";
}

const STATUS_MESSAGES = {
  stopping: "Disconnecting current model...",
  starting: "Starting new container...",
  waiting: "Waiting for model to load...",
  downloading: "Downloading the model, first use only (about 670 MB)...",
  loading: "Loading the model...",
  ready: "Model ready!",
  error: "Switch failed.",
};

function pollModelStatus(prevModel) {
  const interval = setInterval(async () => {
    try {
      const data = await fetch("/api/models/status").then((r) => r.json());
      const base = STATUS_MESSAGES[data.status] || data.status;
      const msg = data.note ? (data.status === "ready" ? data.note : `${data.note}. ${base}`) : base;
      showModelStatus(msg);

      if (data.status === "ready") {
        clearInterval(interval);
        modelSelect.dataset.prev = modelSelect.value;
        modelSelect.disabled = false;
        // The switch downloaded the model: redraw the list so its mark follows.
        loadModelSwitcher();
        setTimeout(hideModelStatus, data.note ? 5000 : 2000);
      } else if (data.status === "error") {
        clearInterval(interval);
        modelSelect.value = prevModel;
        modelSelect.disabled = false;
        showModelStatus("Error: " + (data.error || "Unknown"));
        setTimeout(hideModelStatus, 5000);
      } else if (data.status === "idle") {
        // Switch already completed or was no-op
        clearInterval(interval);
        modelSelect.disabled = false;
        hideModelStatus();
      }
    } catch {
      // Network error during poll, keep trying
    }
  }, 2000);
}
