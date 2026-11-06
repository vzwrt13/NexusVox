/* NexusVox Dashboard — Review tab: keyboard-driven review */

// ── Review Transcriptions (Review Tab) ───────────────────────────────
// Keyboard-driven review: the top card is "active". Space plays its audio,
// Enter accepts it, E opens the correction textarea (Ctrl+Enter submits,
// Esc reverts). After each submit the next card becomes active and autoplays.
let reviewSessionCount = 0;
let reviewRemainingOlder = 0;
let reviewAudio = null;

async function loadReviewTranscriptions() {
  const container = document.getElementById("review-list");
  container.innerHTML = "";
  reviewSessionCount = 0;
  await loadMoreReviewTranscriptions();
}

function updateReviewProgress() {
  const onScreen = document.querySelectorAll("#review-list .flagged-card[data-id]").length;
  const el = document.getElementById("review-progress");
  el.textContent = `${reviewSessionCount} reviewed this session \u00B7 ${onScreen + reviewRemainingOlder} left`;
}

function activeReviewCard() {
  return document.querySelector("#review-list .flagged-card[data-id]");
}

function activateTopReviewCard(autoplay) {
  document.querySelectorAll("#review-list .review-active").forEach((c) => c.classList.remove("review-active"));
  const card = activeReviewCard();
  if (!card) return;
  card.classList.add("review-active");
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (autoplay) toggleReviewAudio(card);
}

function stopReviewAudio() {
  if (!reviewAudio) return;
  reviewAudio.audio.pause();
  reviewAudio.btn.textContent = "\u25B6 Play";
  reviewAudio = null;
}

function toggleReviewAudio(card) {
  const btn = card.querySelector(".flagged-play-btn");
  if (reviewAudio && reviewAudio.btn === btn) {
    stopReviewAudio();
    return;
  }
  stopReviewAudio();
  const audio = new Audio(`/api/audio/${card.dataset.id}`);
  reviewAudio = { audio, btn };
  btn.textContent = "\u25A0 Stop";
  const reset = () => { if (reviewAudio && reviewAudio.audio === audio) stopReviewAudio(); };
  audio.onended = reset;
  audio.onerror = reset;
  audio.play().catch(reset);
}

async function retryReviewTranscription(card, btn) {
  btn.disabled = true;
  btn.textContent = "Transcribing...";
  let res;
  try {
    res = await fetch(`/api/review/${card.dataset.id}/retry`, { method: "POST" }).then((r) => r.json());
  } catch (err) {
    res = { ok: false, error: `Retry failed: ${err}` };
  } finally {
    btn.disabled = false;
    btn.textContent = "\u21BB Retry";
  }
  if (!res.ok) {
    alert(res.error);
    return;
  }
  // The new transcript goes into the correction box; submitting it is the user's call.
  markReviewWrong(card);
  card.querySelector(".review-correction").value = res.text;
}

function markReviewWrong(card) {
  const cb = card.querySelector(".review-correct-cb");
  cb.checked = false;
  toggleReviewTextarea(cb);
  card.querySelector(".review-correction").focus();
}

function markReviewCorrect(card) {
  const cb = card.querySelector(".review-correct-cb");
  cb.checked = true;
  toggleReviewTextarea(cb);
  card.querySelector(".review-correction").blur();
}

document.addEventListener("keydown", (e) => {
  if (!document.getElementById("tab-review").classList.contains("active")) return;
  const card = activeReviewCard();
  if (!card) return;
  const inTextarea = e.target === card.querySelector(".review-correction");

  if (inTextarea) {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      submitReview(card.dataset.id, card.querySelector(".flagged-save-btn"));
    } else if (e.key === "Escape") {
      e.preventDefault();
      markReviewCorrect(card);
    }
    return;
  }
  if (e.target.matches("input, textarea, select") || e.ctrlKey || e.altKey || e.metaKey) return;

  if (e.key === " ") {
    e.preventDefault();
    toggleReviewAudio(card);
  } else if (e.key === "Enter") {
    e.preventDefault();
    markReviewCorrect(card);
    submitReview(card.dataset.id, card.querySelector(".flagged-save-btn"));
  } else if (e.key === "e" || e.key === "E") {
    e.preventDefault();
    markReviewWrong(card);
  }
});

async function loadMoreReviewTranscriptions() {
  const container = document.getElementById("review-list");
  const loadMore = document.getElementById("review-load-more");
  const cards = container.querySelectorAll(".flagged-card[data-id]");
  const lastId = cards.length ? cards[cards.length - 1].dataset.id : null;
  const url = lastId ? `/api/review?before_id=${lastId}` : "/api/review";

  loadMore.disabled = true;
  const { items: data, remaining } = await fetch(url).then((r) => r.json());

  loadMore.hidden = remaining === 0;
  loadMore.disabled = false;
  loadMore.textContent = `Load more (${remaining} older)`;
  reviewRemainingOlder = remaining;

  if (!data.length && !cards.length) {
    container.innerHTML = '<p class="empty-state">No unreviewed recordings. All caught up!</p>';
    updateReviewProgress();
    return;
  }

  container.insertAdjacentHTML("beforeend", data.map((item) => {
    const isFlagged = item.flagged === 1;
    const defaultCorrect = !isFlagged;
    return `
    <div class="flagged-card${isFlagged ? " review-card-flagged" : ""}" data-id="${item.id}">
      <div class="flagged-meta">
        <span>${item.created_at ? new Date(item.created_at).toLocaleString() : ""}</span>
        <span class="flagged-lang">${(item.language || "").toUpperCase()}</span>
        ${item.confidence != null ? `<span class="flagged-conf">${(item.confidence * 100).toFixed(1)}%</span>` : ""}
        <span class="review-duration">${formatDuration(item.duration_ms)}</span>
      </div>
      <div class="flagged-original"><strong>Original:</strong> ${escapeHtml(item.text)}</div>
      <button class="flagged-play-btn" onclick="toggleReviewAudio(this.closest('.flagged-card'))">&#9654; Play</button>
      <button class="flagged-play-btn review-retry-btn" title="Transcribe the stored audio again with the current model"
        onclick="retryReviewTranscription(this.closest('.flagged-card'), this)">&#8635; Retry</button>
      <div class="review-checkbox-row">
        <label class="review-label">
          <input type="checkbox" class="review-correct-cb" ${defaultCorrect ? "checked" : ""}
            onchange="toggleReviewTextarea(this)">
          Transcription is correct
        </label>
      </div>
      <textarea class="flagged-textarea review-correction" rows="2"
        placeholder="Enter correct text..." style="display: ${defaultCorrect ? "none" : "block"}">${escapeHtml(item.corrected_text || "")}</textarea>
      <button class="flagged-save-btn" onclick="submitReview(${item.id}, this)">Submit Review</button>
    </div>`;
  }).join(""));
  updateReviewProgress();
  if (!cards.length) activateTopReviewCard(false);
}

function showReviewSuggestions(suggestions) {
  const box = document.getElementById("review-suggestions");
  box.innerHTML = "<span>Add to dictionary?</span>" + suggestions.map((sg) => `
    <button class="flagged-play-btn" onclick="acceptReviewSuggestion(this)"
      data-wrong="${escapeHtml(sg.wrong)}" data-right="${escapeHtml(sg.right)}">
      <s>${escapeHtml(sg.wrong)}</s> &rarr; ${escapeHtml(sg.right)}
    </button>`).join("") + '<button class="flagged-play-btn" onclick="dismissReviewSuggestions()">Dismiss</button>';
  box.hidden = false;
}

async function acceptReviewSuggestion(btn) {
  btn.disabled = true;
  if (await addDictionaryEntry(btn.dataset.wrong, btn.dataset.right)) {
    btn.textContent = "Added";
  } else {
    btn.disabled = false;
  }
}

function dismissReviewSuggestions() {
  const box = document.getElementById("review-suggestions");
  box.hidden = true;
  box.innerHTML = "";
}

function toggleReviewTextarea(cb) {
  const card = cb.closest(".flagged-card");
  card.querySelector(".review-correction").style.display = cb.checked ? "none" : "block";
}

async function submitReview(id, btn) {
  const card = btn.closest(".flagged-card");
  const isCorrect = card.querySelector(".review-correct-cb").checked;
  const correctedText = card.querySelector(".review-correction").value || null;

  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = "Submitting...";
  stopReviewAudio();
  try {
    const res = await fetch(`/api/review/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_correct: isCorrect, corrected_text: isCorrect ? null : correctedText }),
    });
    const data = await res.json();
    if (data.ok) {
      reviewSessionCount += 1;
      if (data.suggestions && data.suggestions.length) showReviewSuggestions(data.suggestions);
      card.style.transition = "opacity 0.3s";
      card.style.opacity = "0";
      setTimeout(async () => {
        card.remove();
        if (!document.getElementById("review-list").children.length) {
          await loadMoreReviewTranscriptions();
        }
        updateReviewProgress();
        activateTopReviewCard(true);
      }, 300);
    } else {
      btn.textContent = "Error";
      btn.disabled = false;
    }
  } catch {
    btn.textContent = "Error";
    btn.disabled = false;
  }
}
