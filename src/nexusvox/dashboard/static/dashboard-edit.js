/* NexusVox Dashboard — Edit tab: flagged transcriptions */

// ── Flagged Transcriptions (Edit Tab) ────────────────────────────────
async function loadFlaggedTranscriptions() {
  const data = await fetch("/api/flagged").then((r) => r.json());
  const container = document.getElementById("flagged-list");

  if (!data.length) {
    container.innerHTML = '<p class="empty-state">No flagged transcriptions yet. Say "nexus flag" after a recording to flag it.</p>';
    return;
  }

  container.innerHTML = data.map((item) => `
    <div class="flagged-card" data-id="${item.id}">
      <div class="flagged-meta">
        <span>${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span>
        <span class="flagged-lang">${(item.language || "").toUpperCase()}</span>
        ${item.confidence != null ? `<span class="flagged-conf">${(item.confidence * 100).toFixed(1)}%</span>` : ""}
      </div>
      <div class="flagged-original"><strong>Original:</strong> ${escapeHtml(item.text)}</div>
      ${item.audio_path ? `<button class="flagged-play-btn" onclick="playAudio(${item.id}, this)">&#9654; Play</button>` : ""}
      <textarea class="flagged-textarea" rows="2" placeholder="Enter correct text...">${escapeHtml(item.corrected_text || "")}</textarea>
      <button class="flagged-save-btn" onclick="saveCorrection(${item.id}, this)">Save</button>
    </div>
  `).join("");
}

function playAudio(tid, btn) {
  const audio = new Audio(`/api/audio/${tid}`);
  btn.disabled = true;
  btn.textContent = "Playing...";
  audio.onended = () => { btn.textContent = "\u25B6 Play"; btn.disabled = false; };
  audio.onerror = () => { btn.textContent = "\u25B6 Play"; btn.disabled = false; };
  audio.play();
}

async function saveCorrection(id, btn) {
  const card = btn.closest(".flagged-card");
  const text = card.querySelector(".flagged-textarea").value;
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    await fetch(`/api/flagged/${id}/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corrected_text: text }),
    });
    btn.textContent = "Saved";
    setTimeout(() => { btn.textContent = "Save"; btn.disabled = false; }, 1500);
  } catch {
    btn.textContent = "Error";
    btn.disabled = false;
  }
}
