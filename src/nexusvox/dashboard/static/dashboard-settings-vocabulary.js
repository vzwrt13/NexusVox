/* NexusVox Dashboard — Settings tab, Vocabulary group: dictionary, polish presets, suggestions */

// ── Vocabulary lists: count, filter, collapse ────────────────────────
const VOCAB_TITLES = { dict: "Dictionary", preset: "Presets" };

// Lower case, composed form, ß as ss: "Straße" is found by "strasse" and "STRASSE", a decomposed ü by an ü.
function vocabFold(s) {
  return String(s ?? "").normalize("NFC").toLowerCase().replace(/ß/g, "ss");
}

function vocabFilter(items, query, fields) {
  const q = vocabFold(query).trim();
  if (!q) return items;
  return items.filter((it) => fields.some((f) => vocabFold(it[f]).includes(q)));
}

function vocabHeader(title, total, shown) {
  return shown === total ? `${title} (${total})` : `${title} (${shown} of ${total})`;
}

function vocabIsCollapsed(key) {
  try { return localStorage.getItem(`vocab-collapsed-${key}`) === "1"; } catch (e) { return false; }
}

function vocabSetCollapsed(key, collapsed) {
  try { localStorage.setItem(`vocab-collapsed-${key}`, collapsed ? "1" : "0"); } catch (e) { /* private mode */ }
}

// Header text, arrow and the hidden state of the body; count is null until the list has loaded.
function vocabApplyState(key, count, shown) {
  const collapsed = vocabIsCollapsed(key);
  const toggle = document.getElementById(`${key}-toggle`);
  const title = count === null ? VOCAB_TITLES[key] : vocabHeader(VOCAB_TITLES[key], count, shown);
  toggle.textContent = `${collapsed ? "▸" : "▾"} ${title}`;
  toggle.setAttribute("aria-expanded", String(!collapsed));
  document.getElementById(`${key}-body`).hidden = collapsed;
  return collapsed;
}

function vocabRender(key, items, fields, emptyText, rowHtml) {
  const filtered = vocabFilter(items, document.getElementById(`${key}-filter`).value, fields);
  // A collapsed list builds no rows; expanding renders it.
  if (vocabApplyState(key, items.length, filtered.length)) return;
  document.getElementById(`${key}-list`).innerHTML = !items.length
    ? `<p class="nexus-empty">${emptyText}</p>`
    : filtered.length ? filtered.map(rowHtml).join("") : '<p class="nexus-empty">No matches.</p>';
}

// Before a new entry is rendered: drop the filter, open the list and show its top.
function vocabReveal(key) {
  document.getElementById(`${key}-filter`).value = "";
  vocabSetCollapsed(key, false);
  document.getElementById(`${key}-list`).scrollTop = 0;
}

// After a render: scroll the row with this id into the visible part of the list.
function vocabScrollTo(key, id) {
  const list = document.getElementById(`${key}-list`);
  const row = list.querySelector(`[data-id="${id}"]`);
  if (row) list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top;
}

function wireVocabBox(key, render) {
  vocabApplyState(key, null, 0);
  document.getElementById(`${key}-toggle`).addEventListener("click", () => {
    const collapse = !vocabIsCollapsed(key);
    vocabSetCollapsed(key, collapse);
    // A hidden filter must not keep narrowing the list.
    if (collapse) document.getElementById(`${key}-filter`).value = "";
    render();
  });
  document.getElementById(`${key}-filter`).addEventListener("input", () => {
    render();
    document.getElementById(`${key}-list`).scrollTop = 0;
  });
}
// ── end of Vocabulary lists

// ── Correction Dictionary ────────────────────────────────────────────
let dictCache = [];

async function loadDictionary() {
  const { entries } = await fetch("/api/dictionary").then((r) => r.json());
  dictCache = entries;
  renderDictionary();
}

function renderDictionary() {
  vocabRender("dict", dictCache, ["wrong", "right"], "No entries yet.", (e) => `
    <div class="dict-row" data-id="${e.id}">
      <span class="dict-wrong">${escapeHtml(e.wrong)}</span>
      <span class="dict-arrow">&rarr;</span>
      <span class="dict-right">${escapeHtml(e.right)}</span>
      <button class="dict-delete" title="Remove" onclick="deleteDictionaryEntry(${e.id})">&#10005;</button>
    </div>`);
}

// A refused entry ({ok: false, error}) shows its error under `anchor` (the dictionary form by default).
async function addDictionaryEntry(wrong, right, anchor = document.getElementById("dict-form")) {
  let res;
  try {
    res = await postSetting("/api/dictionary", { wrong, right }).then((r) => r.json());
  } catch {
    res = { ok: false, error: "The entry could not be saved." };
  }
  showNotice(anchor, "down", res.ok ? "" : res.error || "The entry was refused.");
  if (res.ok) {
    vocabReveal("dict");
    loadDictionary();
  }
  return res.ok;
}

async function deleteDictionaryEntry(id) {
  await fetch(`/api/dictionary/${id}`, { method: "DELETE" });
  loadDictionary();
}

// ── Polish presets ───────────────────────────────────────────────────
let presetCache = [];

async function loadPolishPresets(revealName = null) {
  const { presets } = await fetch("/api/polish-presets").then((r) => r.json());
  presetCache = presets;
  renderPolishPresets();
  // Presets are sorted by name: a new or renamed one can sit below the visible part.
  const saved = revealName && presets.find((p) => vocabFold(p.name) === vocabFold(revealName));
  if (saved) vocabScrollTo("preset", saved.id);
}

function renderPolishPresets() {
  vocabRender("preset", presetCache, ["name", "instruction"], "No presets yet.", (p) => `
    <div class="dict-row" data-id="${p.id}">
      <span class="dict-wrong">${escapeHtml(p.name)}${p.is_default ? " (default)" : ""}</span>
      <span class="snippet-text">${escapeHtml(p.instruction)}</span>
      <button class="flagged-play-btn" onclick="editPolishPreset(${p.id})">Edit</button>
      <button class="dict-delete" title="Remove" onclick="deletePolishPreset(${p.id})">&#10005;</button>
    </div>`);
}

function resetPresetForm() {
  document.getElementById("preset-id").value = "";
  document.getElementById("preset-name").value = "";
  document.getElementById("preset-instruction").value = "";
  document.getElementById("preset-default").checked = false;
  document.getElementById("preset-save").textContent = "Add";
  document.getElementById("preset-cancel").hidden = true;
}

function editPolishPreset(id) {
  const p = presetCache.find((x) => x.id === id);
  if (!p) return;
  document.getElementById("preset-id").value = p.id;
  document.getElementById("preset-name").value = p.name;
  document.getElementById("preset-instruction").value = p.instruction;
  document.getElementById("preset-default").checked = p.is_default;
  document.getElementById("preset-save").textContent = "Save";
  document.getElementById("preset-cancel").hidden = false;
  document.getElementById("preset-instruction").focus();
}

async function deletePolishPreset(id) {
  await fetch(`/api/polish-presets/${id}`, { method: "DELETE" });
  if (document.getElementById("preset-id").value === String(id)) resetPresetForm();
  loadPolishPresets();
}

wireVocabBox("dict", renderDictionary);
wireVocabBox("preset", renderPolishPresets);

document.getElementById("preset-cancel").addEventListener("click", resetPresetForm);

document.getElementById("preset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("preset-id").value;
  const savedName = document.getElementById("preset-name").value.trim();
  const res = await postSetting("/api/polish-presets", {
    id: id ? Number(id) : null,
    name: document.getElementById("preset-name").value,
    instruction: document.getElementById("preset-instruction").value,
    is_default: document.getElementById("preset-default").checked,
  }).then((r) => r.json());
  showNotice(document.getElementById("preset-form"), "down", res.ok ? "" : res.error);
  if (res.ok) {
    resetPresetForm();
    vocabReveal("preset");
    loadPolishPresets(savedName);
  }
});

async function loadDictionarySuggestions() {
  const { suggestions } = await fetch("/api/dictionary/suggestions").then((r) => r.json());
  const box = document.getElementById("dict-suggestions");
  box.innerHTML = suggestions.length ? "<p class=\"settings-hint\">Learned from your edits:</p>" + suggestions.map((s) => `
    <div class="dict-row" data-id="${s.id}">
      <span class="dict-wrong">${escapeHtml(s.wrong)}</span>
      <span class="dict-arrow">&rarr;</span>
      <span class="dict-right">${escapeHtml(s.right)}</span>
      <button class="flagged-play-btn" onclick="acceptDictionarySuggestion(${s.id})">Add</button>
      <button class="dict-delete" title="Dismiss" onclick="dismissDictionarySuggestion(${s.id})">&#10005;</button>
    </div>`).join("") : "";
}

async function acceptDictionarySuggestion(id) {
  await fetch(`/api/dictionary/suggestions/${id}/accept`, { method: "POST" });
  loadDictionary();
  loadDictionarySuggestions();
}

async function dismissDictionarySuggestion(id) {
  await fetch(`/api/dictionary/suggestions/${id}`, { method: "DELETE" });
  loadDictionarySuggestions();
}

document.getElementById("dict-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const wrongEl = document.getElementById("dict-wrong");
  const rightEl = document.getElementById("dict-right");
  if (await addDictionaryEntry(wrongEl.value, rightEl.value)) {
    wrongEl.value = "";
    rightEl.value = "";
    wrongEl.focus();
  }
});
