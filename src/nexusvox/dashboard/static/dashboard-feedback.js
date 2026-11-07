// ── Feedback tab: send a report on click, with an optional log ───────
// Nothing is sent except by the Send click. The preview asks the server to build the same report as text and
// sends nothing; when it is open, Send posts exactly that report (by its token). On any failure the typed text
// stays where it is.
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
let feedbackPreviewSeq = 0; // a reply to an older request never overwrites a newer one
let feedbackShown = null; // { body, token } of the report the open preview displays

// The in-app form needs a sending address the app trusts; without one only the web form is offered.
function applyFeedbackAvailability(settings) {
  feedbackForm.hidden = !settings.feedback_app_send;
}

function feedbackBody() {
  return JSON.stringify({
    kind: feedbackKind.value,
    message: feedbackMessage.value,
    include_log: feedbackIncludeLog.checked,
  });
}

async function feedbackRequest(path, body) {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body });
    return await r.json();
  } catch (e) {
    return { error: "dashboard", message: "The dashboard could not be reached. Your text is kept." };
  }
}

function showFeedbackStatus(state, text) {
  setStatus(feedbackStatus, state, text);
  feedbackStatus.hidden = !text;
}

function hideFeedbackPreview() {
  feedbackPreview.hidden = true;
  feedbackShown = null;
  feedbackPreviewToggle.textContent = "Show what will be sent";
}

async function refreshFeedbackPreview() {
  if (feedbackPreview.hidden) return;
  const seq = ++feedbackPreviewSeq;
  const body = feedbackBody();
  const reply = await feedbackRequest("/api/feedback/preview", body);
  if (seq !== feedbackPreviewSeq || feedbackPreview.hidden) return;
  feedbackShown = reply.ok ? { body, token: reply.token } : null;
  feedbackPreview.textContent = reply.ok ? reply.preview : reply.message || "Nothing to show yet.";
}

function scheduleFeedbackPreview() {
  if (feedbackPreview.hidden) return;
  feedbackShown = null; // what is on screen no longer matches the inputs
  clearTimeout(feedbackPreviewTimer);
  feedbackPreviewTimer = setTimeout(refreshFeedbackPreview, 300);
}

feedbackPreviewToggle.addEventListener("click", () => {
  if (feedbackPreview.hidden) {
    feedbackPreview.hidden = false;
    feedbackPreviewToggle.textContent = "Hide what will be sent";
    refreshFeedbackPreview();
  } else {
    hideFeedbackPreview();
  }
});
feedbackMessage.addEventListener("input", scheduleFeedbackPreview);
feedbackKind.addEventListener("change", scheduleFeedbackPreview);
feedbackIncludeLog.addEventListener("change", scheduleFeedbackPreview);

feedbackForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (feedbackSendButton.disabled) return;
  let body = feedbackBody();
  if (!feedbackPreview.hidden) {
    // The preview is open: only what it shows may go out.
    if (!feedbackShown || feedbackShown.body !== body) {
      clearTimeout(feedbackPreviewTimer);
      refreshFeedbackPreview();
      showFeedbackStatus(undefined, "The preview was out of date. Check it and click Send again.");
      return;
    }
    body = JSON.stringify({ ...JSON.parse(body), token: feedbackShown.token });
  }
  feedbackSendButton.disabled = true;
  showFeedbackStatus(undefined, "Sending…");
  const reply = await feedbackRequest("/api/feedback/send", body);
  feedbackSendButton.disabled = false;
  if (reply.ok) {
    const askedForLog = feedbackIncludeLog.checked;
    feedbackMessage.value = "";
    feedbackIncludeLog.checked = false; // every report asks again before the log goes along
    hideFeedbackPreview();
    showFeedbackStatus(
      "ok",
      reply.log_included
        ? "Thank you, your report and the log were sent."
        : "Thank you, your report was sent." + (askedForLog ? " No log was available to include." : ""),
    );
  } else {
    if (reply.error === "stale") refreshFeedbackPreview();
    showFeedbackStatus("down", reply.message || "The report could not be sent. Your text is kept.");
  }
});

openFeedbackFormButton.addEventListener("click", () => {
  if (feedbackUrl) window.open(feedbackUrl, "_blank", "noopener,noreferrer");
});
