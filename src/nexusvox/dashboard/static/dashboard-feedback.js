// ── Feedback tab: send a report on click, with an optional log ───────
// Nothing is sent except by the Send click. The preview asks the server to build the same report as text and
// sends nothing. On any failure the typed text stays where it is.
const feedbackForm = document.getElementById("feedback-form");
const feedbackKind = document.getElementById("feedback-kind");
const feedbackMessage = document.getElementById("feedback-message");
const feedbackIncludeLog = document.getElementById("feedback-include-log");
const feedbackSendButton = document.getElementById("send-feedback");
const feedbackPreviewToggle = document.getElementById("feedback-preview-toggle");
const feedbackPreview = document.getElementById("feedback-preview");
const feedbackStatus = document.getElementById("feedback-status");
const openFeedbackFormButton = document.getElementById("open-feedback-form");
let feedbackPreviewTimer = null;

function feedbackBody() {
  return JSON.stringify({
    kind: feedbackKind.value,
    message: feedbackMessage.value,
    include_log: feedbackIncludeLog.checked,
  });
}

async function feedbackRequest(path) {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: feedbackBody() });
    return await r.json();
  } catch (e) {
    return { error: "dashboard", message: "The dashboard could not be reached. Your text is kept." };
  }
}

function showFeedbackStatus(state, text) {
  setStatus(feedbackStatus, state, text);
  feedbackStatus.hidden = !text;
}

async function refreshFeedbackPreview() {
  if (feedbackPreview.hidden) return;
  const reply = await feedbackRequest("/api/feedback/preview");
  feedbackPreview.textContent = reply.ok ? reply.preview : reply.message || "Nothing to show yet.";
}

function scheduleFeedbackPreview() {
  clearTimeout(feedbackPreviewTimer);
  feedbackPreviewTimer = setTimeout(refreshFeedbackPreview, 300);
}

feedbackPreviewToggle.addEventListener("click", () => {
  feedbackPreview.hidden = !feedbackPreview.hidden;
  feedbackPreviewToggle.textContent = feedbackPreview.hidden ? "Show what will be sent" : "Hide what will be sent";
  refreshFeedbackPreview();
});
[feedbackKind, feedbackMessage, feedbackIncludeLog].forEach((el) => {
  el.addEventListener("input", scheduleFeedbackPreview);
  el.addEventListener("change", scheduleFeedbackPreview);
});

feedbackForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (feedbackSendButton.disabled) return;
  feedbackSendButton.disabled = true;
  showFeedbackStatus(undefined, "Sending…");
  const reply = await feedbackRequest("/api/feedback/send");
  feedbackSendButton.disabled = false;
  if (reply.ok) {
    feedbackMessage.value = "";
    feedbackPreview.hidden = true;
    feedbackPreviewToggle.textContent = "Show what will be sent";
    showFeedbackStatus(
      "ok",
      reply.log_included
        ? "Thank you, your report and the log were sent."
        : "Thank you, your report was sent." + (feedbackIncludeLog.checked ? " No log was available to include." : ""),
    );
  } else {
    showFeedbackStatus("down", reply.message || "The report could not be sent. Your text is kept.");
  }
});

openFeedbackFormButton.addEventListener("click", () => {
  if (feedbackUrl) window.open(feedbackUrl, "_blank", "noopener,noreferrer");
});
