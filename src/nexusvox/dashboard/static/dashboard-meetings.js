/* NexusVox Dashboard — Meeting recording in the Recordings tab: start/stop, live transcript, meeting settings */

// ── Meetings ─────────────────────────────────────────────────────────
const meetingToggleBtn = document.getElementById("meeting-toggle");
const meetingTimer = document.getElementById("meeting-timer");
const meetingIndicator = document.getElementById("meeting-indicator");
const meetingStatus = document.getElementById("meeting-status");
const meetingProgress = document.getElementById("meeting-progress");
const meetingProgressBar = document.getElementById("meeting-progress-bar");
const meetingConsent = document.getElementById("meeting-consent");
const meetingConsentToggle = document.getElementById("meeting-consent-toggle");
const meetingDictationToggle = document.getElementById("meeting-dictation-toggle");
const meetingLiveToggle = document.getElementById("meeting-live-toggle");
const meetingStoreAudioToggle = document.getElementById("meeting-store-audio-toggle");
const meetingLiveBox = document.getElementById("meeting-live");
const meetingLiveText = document.getElementById("meeting-live-text");
const meetingLiveStatus = document.getElementById("meeting-live-status");
let meetingLiveTimer = null;
let meetingLiveAfter = 0;
let meetingLiveBusy = false;
let meetingLiveGeneration = 0;
const meetingHotkeyMods = Array.from(document.querySelectorAll("#meeting-hotkey-mods input"));
const meetingHotkeyKey = document.getElementById("meeting-hotkey-key");
const meetingHotkeyStatus = document.getElementById("meeting-hotkey-status");
const meetingAltHint = document.getElementById("meeting-hotkey-alt-hint");
const meetingMicSelect = document.getElementById("meeting-mic-select");
const meetingOutputSelect = document.getElementById("meeting-output-select");
// The Recordings tab's options popover offers the same two devices; the Settings selects stay the stored ones.
const recMicSelect = document.getElementById("rec-mic-select");
const recOutputSelect = document.getElementById("rec-output-select");
const recRecordingBox = document.getElementById("rec-recording");
let meetingRecording = false;
let meetingElapsed = 0;
let meetingTick = null;
let meetingPoll = null;
let meetingJobTimer = null;
let meetingConsentHint = true;

function formatClock(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${m}:${s}` : `${m}:${s}`;
}

function showMeetingStatus(text, cls) {
  meetingStatus.hidden = !text;
  setStatus(meetingStatus, cls, text);
}

// Live transcript: poll the new segments every 2 s while a meeting records. Text goes in with textContent only.
function appendLiveSegments(segments) {
  const atBottom = meetingLiveText.scrollTop + meetingLiveText.clientHeight >= meetingLiveText.scrollHeight - 8;
  for (const seg of segments) {
    if (seg.n < meetingLiveAfter) continue; // already shown (overlapping polls)
    const p = document.createElement("p");
    const who = document.createElement("span");
    who.className = "who " + (seg.speaker === "mic" ? "me" : "system");
    who.textContent = seg.label + ":";
    p.append(who, document.createTextNode(seg.text));
    meetingLiveText.append(p);
    meetingLiveAfter = seg.n + 1;
  }
  if (atBottom) meetingLiveText.scrollTop = meetingLiveText.scrollHeight;
}

async function pollMeetingLive() {
  if (meetingLiveBusy) return; // one request at a time: two in flight would both append the same segments
  meetingLiveBusy = true;
  const generation = meetingLiveGeneration;
  try {
    const live = await fetch("/api/meetings/live?after=" + meetingLiveAfter).then((r) => r.json());
    if (generation !== meetingLiveGeneration) return; // a new meeting started while this was in flight
    // A meeting stopped from the hotkey while another tab is shown never reaches renderMeetingState.
    // The poll runs on until the worker has ended too: a window in flight at Stop still adds its text.
    if (!live.recording && !(live.status && live.status.active) && meetingLiveTimer) {
      clearInterval(meetingLiveTimer);
      meetingLiveTimer = null;
    }
    meetingLiveBox.hidden = !live.status;
    appendLiveSegments(live.segments);
    if (live.status) {
      const st = live.status;
      meetingLiveStatus.textContent = st.error
        ? "Live transcription failed for the last window: " + st.error
        : st.skipped_s > 0
          ? `Model load ${Math.round(st.load * 100)}%. The live view skipped ${st.skipped_s} s of audio to keep up; the saved transcript is complete.`
          : `Model load ${Math.round(st.load * 100)}%.`;
    }
  } catch (err) {
    console.error("Live transcript failed:", err);
  } finally {
    meetingLiveBusy = false;
  }
}

function setMeetingLivePolling(on) {
  if (on && !meetingLiveTimer) {
    meetingLiveAfter = 0;
    meetingLiveGeneration += 1;
    meetingLiveBusy = false;
    meetingLiveText.textContent = "";
    pollMeetingLive();
    meetingLiveTimer = setInterval(pollMeetingLive, 2000);
  } else if (!on && meetingLiveTimer) {
    pollMeetingLive(); // the timer stops itself once the worker has ended (see pollMeetingLive)
  }
}

function renderMeetingState(status) {
  meetingRecording = !!status.recording;
  setMeetingLivePolling(meetingRecording);
  meetingElapsed = status.elapsed_s || 0;
  meetingIndicator.classList.toggle("recording", meetingRecording);
  recRecordingBox.hidden = !meetingRecording;
  meetingTimer.textContent = formatClock(meetingElapsed);
  meetingToggleBtn.textContent = meetingRecording ? "Stop recording" : "Start recording";
  meetingToggleBtn.classList.toggle("is-recording", meetingRecording);
  if (meetingRecording && !meetingTick) {
    // The server's elapsed time is the truth; between polls the timer counts on its own.
    meetingTick = setInterval(() => {
      meetingElapsed += 1;
      meetingTimer.textContent = formatClock(meetingElapsed);
    }, 1000);
  } else if (!meetingRecording && meetingTick) {
    clearInterval(meetingTick);
    meetingTick = null;
  }
}

async function refreshMeetingStatus() {
  try {
    renderMeetingState(await fetch("/api/meetings/status").then((r) => r.json()));
  } catch (err) {
    console.error("Meeting status failed:", err);
  }
}

async function startMeeting() {
  meetingToggleBtn.disabled = true;
  showMeetingStatus("");
  try {
    const result = await fetch("/api/meetings/start", { method: "POST" }).then((r) => r.json());
    if (!result.ok) showMeetingStatus("✕ " + result.error, "down");
    else showMeetingStatus("● Recording. Dictation still works.", "ok");
    await refreshMeetingStatus();
  } finally {
    meetingToggleBtn.disabled = false;
  }
}

async function stopMeeting() {
  meetingToggleBtn.disabled = true;
  try {
    const result = await fetch("/api/meetings/stop", { method: "POST" }).then((r) => r.json());
    await refreshMeetingStatus();
    if (!result.ok && !result.busy) {
      showMeetingStatus("✕ " + result.error, "down");
    } else if (result.busy) {
      showMeetingStatus("● " + result.error + ". Transcribe it from the list below once that is done.", "warn");
      loadFileTranscriptions();
    } else {
      trackMeetingJob(result.job_id);
    }
  } finally {
    meetingToggleBtn.disabled = false;
  }
}

// Stop starts the transcription job; its progress is shown here until the meeting appears in the list, where
// it then opens in the detail pane.
function trackMeetingJob(jobId) {
  clearInterval(meetingJobTimer);
  meetingProgress.style.display = "";
  const stop = () => {
    clearInterval(meetingJobTimer);
    meetingProgress.style.display = "none";
    loadFileTranscriptions();
  };
  const poll = async () => {
    let resp;
    try {
      resp = await fetch(`/api/file-transcribe/${jobId}`);
    } catch (err) {
      return; // no contact for a moment: retried on the next tick
    }
    if (!resp.ok) {
      // The job is gone (the app restarted, or it dropped out of the last ten): nothing says it succeeded.
      stop();
      showMeetingStatus("✕ Lost track of the transcription. See below whether the meeting was transcribed.", "down");
      return;
    }
    const job = await resp.json();
    meetingProgressBar.value = Math.round((job.percent || 0) * 100);
    if (job.state === "running") {
      showMeetingStatus(job.message || "Transcribing...", "");
      return;
    }
    const savedId = job.state === "done" && job.result ? job.result.id : undefined;
    if (savedId !== undefined && savedId !== null) recSelectedId = String(savedId);
    stop();
    if (job.state === "done") showMeetingStatus("● Meeting transcribed.", "ok");
    else showMeetingStatus("✕ " + (job.error || "Transcription failed"), "down");
  };
  meetingJobTimer = setInterval(poll, 1000);
  poll();
}

// Recordings without a transcript: transcribe them or delete them (confirmation inside the card).
async function loadPendingMeetings() {
  const block = document.getElementById("meeting-pending");
  const list = document.getElementById("meeting-pending-list");
  const pending = await fetch("/api/meetings/pending").then((r) => r.json());
  block.hidden = !pending.length;
  list.innerHTML = pending.map((p) => `
    <div class="flagged-card" data-name="${escapeHtml(p.name)}">
      <div class="flagged-meta"><span>${p.started_at ? new Date(p.started_at).toLocaleString() : ""}</span></div>
      <div class="upload-filename">${escapeHtml(p.name)}</div>
      <div class="upload-delete-row">
        <button type="button" class="period-btn pending-transcribe">Transcribe</button>
        <button type="button" class="period-btn upload-delete-btn pending-delete">Delete</button>
      </div>
      <p class="pending-error" hidden></p>
    </div>`).join("");
  list.querySelectorAll(".flagged-card").forEach((card) => {
    const name = card.dataset.name;
    const row = card.querySelector(".upload-delete-row");
    const error = card.querySelector(".pending-error");
    const fail = (text) => { setStatus(error, "down", "✕ " + text); error.hidden = false; };
    card.querySelector(".pending-transcribe").addEventListener("click", async () => {
      error.hidden = true;
      const resp = await fetch(`/api/meetings/pending/${encodeURIComponent(name)}/transcribe`, { method: "POST" });
      const result = await resp.json();
      if (!resp.ok) return fail(result.error || "Could not start the transcription");
      trackMeetingJob(result.job_id);
      loadPendingMeetings();
    });
    card.querySelector(".pending-delete").addEventListener("click", () => {
      row.innerHTML = `
        <span class="upload-delete-ask">Delete this recording and its audio files? This cannot be undone.</span>
        <button type="button" class="period-btn upload-delete-btn pending-delete-confirm">Delete</button>
        <button type="button" class="period-btn pending-delete-cancel">Cancel</button>`;
      row.querySelector(".pending-delete-cancel").addEventListener("click", loadPendingMeetings);
      row.querySelector(".pending-delete-confirm").addEventListener("click", async () => {
        const resp = await fetch(`/api/meetings/pending/${encodeURIComponent(name)}`, { method: "DELETE" });
        if (resp.ok) return loadPendingMeetings();
        fail((await resp.json()).error || "Delete failed");
      });
    });
  });
}

meetingToggleBtn.addEventListener("click", () => {
  if (meetingRecording) stopMeeting();
  else if (meetingConsentHint) meetingConsent.hidden = false;
  else startMeeting();
});

document.getElementById("meeting-consent-cancel").addEventListener("click", () => {
  meetingConsent.hidden = true;
});

document.getElementById("meeting-consent-ok").addEventListener("click", async () => {
  meetingConsent.hidden = true;
  if (document.getElementById("meeting-consent-skip").checked) {
    const s = await postSetting("/api/settings/meeting-consent-hint", { enabled: false }).then((r) => r.json());
    meetingConsentHint = s.meeting_consent_hint;
    meetingConsentToggle.checked = meetingConsentHint;
    document.getElementById("meeting-consent-skip").checked = false;
  }
  startMeeting();
});

meetingConsentToggle.addEventListener("change", async () => {
  const s = await postSetting("/api/settings/meeting-consent-hint", { enabled: meetingConsentToggle.checked }).then((r) => r.json());
  meetingConsentHint = s.meeting_consent_hint;
});

meetingDictationToggle.addEventListener("change", async () => {
  const wanted = meetingDictationToggle.checked;
  try {
    const r = await postSetting("/api/settings/meeting-leave-dictation-out", { enabled: wanted });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    meetingDictationToggle.checked = (await r.json()).meeting_leave_dictation_out;
  } catch (err) {
    meetingDictationToggle.checked = !wanted; // not saved: show the setting that is still in force
  }
});

function renderMeetingHotkey(s) {
  const keycaps = document.getElementById("meeting-hotkey-keycaps");
  keycaps.replaceChildren();
  describeHotkey(s.meeting_modifiers, s.meeting_key).split("+").forEach((name, i) => {
    if (i) keycaps.append(Object.assign(document.createElement("span"), { className: "keycaps-plus", textContent: "+" }));
    keycaps.append(Object.assign(document.createElement("kbd"), { textContent: name }));
  });
  if (!meetingHotkeyKey.options.length) {
    for (const k of s.hotkey_key_options || []) meetingHotkeyKey.append(new Option(k.replace("_", " "), k));
  }
  for (const cb of meetingHotkeyMods) cb.checked = s.meeting_modifiers.includes(cb.value);
  meetingHotkeyKey.value = s.meeting_key;
  renderAltHint(meetingAltHint, s.meeting_modifiers);
}

async function saveMeetingHotkey() {
  const modifiers = meetingHotkeyMods.filter((cb) => cb.checked).map((cb) => cb.value);
  const s = await postSetting("/api/settings/meeting-hotkey", { modifiers, key: meetingHotkeyKey.value }).then((r) => r.json());
  renderMeetingHotkey(s);
  meetingHotkeyStatus.hidden = false;
  if (s.error) {
    setStatus(meetingHotkeyStatus, "down");
    meetingHotkeyStatus.textContent = "✕ " + s.error;
  } else {
    setStatus(meetingHotkeyStatus, "ok");
    meetingHotkeyStatus.textContent = "● Meeting hotkey is now " + describeHotkey(s.meeting_modifiers, s.meeting_key);
  }
}

for (const cb of meetingHotkeyMods) cb.addEventListener("change", saveMeetingHotkey);
meetingHotkeyKey.addEventListener("change", saveMeetingHotkey);

function fillDeviceSelect(select, names, chosen) {
  select.replaceChildren(new Option("System default", ""));
  const all = chosen && !names.includes(chosen) ? [...names, chosen] : names;
  for (const name of all) select.append(new Option(name, name));
  select.value = chosen || "";
}

async function saveMeetingDevices() {
  await postSetting("/api/settings/meeting-devices", { mic: meetingMicSelect.value, output: meetingOutputSelect.value });
  syncRecordingDevices();
}
meetingMicSelect.addEventListener("change", saveMeetingDevices);
meetingOutputSelect.addEventListener("change", saveMeetingDevices);
// A choice in the popover goes through the Settings selects, so both always show the stored device.
recMicSelect.addEventListener("change", () => {
  meetingMicSelect.value = recMicSelect.value;
  saveMeetingDevices();
});
recOutputSelect.addEventListener("change", () => {
  meetingOutputSelect.value = recOutputSelect.value;
  saveMeetingDevices();
});

// A meeting toggle that saves on change and shows the stored value (or the one still in force on failure).
function bindMeetingToggle(toggle, url, key) {
  toggle.addEventListener("change", async () => {
    const wanted = toggle.checked;
    try {
      const r = await postSetting(url, { enabled: wanted });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toggle["checked"] = (await r.json())[key];
    } catch (err) {
      toggle.checked = !wanted; // not saved: show the setting that is still in force
    }
  });
}
bindMeetingToggle(meetingStoreAudioToggle, "/api/settings/meeting-store-audio", "meeting_store_audio");
bindMeetingToggle(meetingLiveToggle, "/api/settings/meeting-live-transcript", "meeting_live_transcript");

// The meeting settings sit in the Settings tab; the Recordings tab's options popover repeats the devices.
async function loadMeetingSettings() {
  const [settings, devices] = await Promise.all([
    fetch("/api/settings").then((r) => r.json()),
    fetch("/api/meetings/devices").then((r) => r.json()),
  ]);
  meetingConsentHint = settings.meeting_consent_hint;
  meetingConsentToggle.checked = meetingConsentHint;
  meetingDictationToggle.checked = settings.meeting_leave_dictation_out;
  meetingLiveToggle.checked = settings.meeting_live_transcript;
  meetingStoreAudioToggle.checked = settings.meeting_store_audio;
  renderMeetingHotkey(settings);
  fillDeviceSelect(meetingMicSelect, devices.inputs, devices.mic_device);
  fillDeviceSelect(meetingOutputSelect, devices.outputs, devices.output_device);
  fillDeviceSelect(recMicSelect, devices.inputs, devices.mic_device);
  fillDeviceSelect(recOutputSelect, devices.outputs, devices.output_device);
}

function syncRecordingDevices() {
  recMicSelect.value = meetingMicSelect.value;
  recOutputSelect.value = meetingOutputSelect.value;
}

// "All meeting settings" link: open the Settings tab, scroll to the Meetings card and flash it.
document.getElementById("meeting-settings-link").addEventListener("click", (e) => {
  e.preventDefault();
  history.replaceState(null, "", "#settings");
  activateTab("settings", { keepScroll: true });
  const card = scrollToSettingsCard("meeting-settings-card");
  card.classList.remove("flash-highlight");
  void card.offsetWidth; // restart the animation when the link is clicked again
  card.classList.add("flash-highlight");
  setTimeout(() => card.classList.remove("flash-highlight"), 2000);
});

// The recording half of opening the Recordings tab (the list and the upload job are dashboard-recordings.js).
async function openMeetingRecording() {
  await loadMeetingSettings();
  await refreshMeetingStatus();
  // A meeting started with the hotkey while this tab is open shows up through the poll.
  clearInterval(meetingPoll);
  meetingPoll = setInterval(() => {
    if (!document.hidden && document.getElementById("tab-recordings").classList.contains("active")) refreshMeetingStatus();
  }, 3000);
}
