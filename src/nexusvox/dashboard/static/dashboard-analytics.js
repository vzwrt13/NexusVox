/* NexusVox Dashboard — Analytics tab: Overview, Your week, and the page of all charts and tables */

// ── Views ────────────────────────────────────────────────────────────
// Three views share the tab: the short Overview (the one it opens on), the weekly report "Your week" and the
// page of every chart and table. The view is kept in the URL (#analytics/week), like the Settings pages.
const ANALYTICS_VIEWS = ["overview", "week", "charts"];
let analyticsView = "overview";

function analyticsViewHash() {
  return "#analytics" + (analyticsView === "overview" ? "" : "/" + analyticsView);
}

// The view a URL names ("#analytics/week"); "#analytics" alone is the Overview.
function analyticsViewFromHash() {
  const [tab, page] = window.location.hash.replace(/^#/, "").split("/");
  if (tab !== "analytics") return analyticsView;
  return ANALYTICS_VIEWS.includes(page) ? page : "overview";
}

function showAnalyticsView(view) {
  analyticsView = view;
  for (const name of ANALYTICS_VIEWS) document.getElementById("analytics-" + name).hidden = name !== view;
  document.querySelectorAll("[data-analytics-view]").forEach((btn) => {
    const on = btn.dataset.analyticsView === view;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });
  if (view === "overview") loadAnalyticsOverview();
  if (view === "week") {
    weekMonday = null; // opened from Home, the tab or the view button: this week, not the one browsed last
    loadWeek();
  }
  if (view === "charts") loadAnalytics();
}

function openAnalyticsTab() {
  showAnalyticsView(analyticsViewFromHash());
}

function chooseAnalyticsView(view) {
  showAnalyticsView(view);
  history.replaceState(null, "", analyticsViewHash());
  window.scrollTo(0, 0);
}

document.querySelectorAll("[data-analytics-view]").forEach((btn) => {
  btn.addEventListener("click", () => chooseAnalyticsView(btn.dataset.analyticsView));
});

for (const [id, view] of [["analytics-more-link", "charts"], ["analytics-back-link", "overview"]]) {
  document.getElementById(id).addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey) return; // the browser opens the href in a new tab
    e.preventDefault();
    chooseAnalyticsView(view);
  });
}

// ── Overview ─────────────────────────────────────────────────────────
// localIsoDate and formatSavedMinutes are Home's (dashboard-home.js loads later; they are used only when a
// view opens).
let overviewRange = "30"; // "7", "14", "30" or "all"
let overviewRequest = 0; // a slow answer for an earlier range must not overwrite a later one

function overviewQuery() {
  if (overviewRange === "all") return "";
  const end = new Date();
  const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - (Number(overviewRange) - 1));
  return "?" + new URLSearchParams({ start: localIsoDate(start), end: localIsoDate(end) });
}

document.querySelectorAll("[data-range]").forEach((btn) => {
  btn.addEventListener("click", () => {
    overviewRange = btn.dataset.range;
    document.querySelectorAll("[data-range]").forEach((b) => {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-pressed", String(b === btn));
    });
    loadAnalyticsOverview();
  });
});

async function loadAnalyticsOverview() {
  const request = ++overviewRequest;
  const query = overviewQuery();
  try {
    const [overview, perDay, apps] = await Promise.all([
      fetch("/api/overview" + query).then((r) => r.json()),
      fetch("/api/words-per-day" + query).then((r) => r.json()),
      fetch("/api/app-breakdown" + query).then((r) => r.json()),
    ]);
    if (request !== overviewRequest) return;
    renderAnalyticsOverview(overview, perDay, apps);
  } catch (err) {
    console.error("Could not load the analytics overview:", err);
  }
}

function renderAnalyticsOverview(overview, perDay, apps) {
  document.getElementById("ov-words").textContent = (overview.total_words || 0).toLocaleString();
  document.getElementById("ov-dictations").textContent = (overview.total_transcriptions || 0).toLocaleString();
  document.getElementById("ov-wpm").textContent = overview.avg_wpm ? Math.round(overview.avg_wpm) : "--";
  document.getElementById("ov-saved").textContent = formatSavedMinutes(overview.time_saved_minutes);
  renderWordsPerDay(perDay);
  renderAppBars(apps);
}

// "2026-11-03" -> a Date on that local day (new Date("2026-11-03") would be UTC midnight).
function parseIsoDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function formatShortDate(iso, withYear = false) {
  const options = withYear ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" };
  return parseIsoDate(iso).toLocaleDateString(undefined, options);
}

function renderWordsPerDay(d) {
  if (charts.wordsPerDay) charts.wordsPerDay.destroy();
  charts.wordsPerDay = null;
  const empty = !d.values.some((v) => v > 0);
  document.getElementById("ov-words-empty").hidden = !empty;
  document.getElementById("ov-words-box").hidden = empty;
  if (empty || !window.Chart) return;
  // "5 Nov" would stand for two bars when the range reaches back more than a year.
  const spansYears = d.labels[0].slice(0, 4) !== d.labels[d.labels.length - 1].slice(0, 4);
  const ctx = document.getElementById("chart-words-per-day").getContext("2d");
  charts.wordsPerDay = new Chart(ctx, {
    type: "bar",
    data: {
      labels: d.labels.map((iso) => formatShortDate(iso, spansYears)),
      datasets: [{ data: d.values, backgroundColor: COLORS.accent, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      plugins: {
        ...chartDefaults.plugins,
        tooltip: { callbacks: { label: (c) => c.parsed.y.toLocaleString() + " words" } },
      },
    },
  });
}

// The five busiest apps as bars of their dictations. The table of every app is on the charts page.
const OVERVIEW_APP_ROWS = 5;

function renderAppBars(rows) {
  const list = document.getElementById("ov-apps");
  list.replaceChildren();
  document.getElementById("ov-apps-empty").hidden = rows.length > 0;
  const shown = rows.slice(0, OVERVIEW_APP_ROWS);
  const most = Math.max(...shown.map((r) => r.count), 1);
  for (const row of shown) {
    const li = document.createElement("li");
    li.className = "app-bar";
    const name = document.createElement("span");
    name.className = "app-bar-name";
    name.textContent = row.app;
    const track = document.createElement("span");
    track.className = "app-bar-track";
    const fill = document.createElement("span");
    fill.className = "app-bar-fill";
    fill.style.width = Math.max((row.count * 100) / most, 2) + "%";
    track.append(fill);
    const count = document.createElement("span");
    count.className = "app-bar-count";
    count.textContent = row.count.toLocaleString();
    li.append(name, track, count);
    list.append(li);
  }
}

// ── Your week ────────────────────────────────────────────────────────
let weekMonday = null; // the shown week's Monday as an ISO date; null is the current week
let weekRequest = 0;

function addDaysIso(iso, days) {
  const d = parseIsoDate(iso);
  d.setDate(d.getDate() + days);
  return localIsoDate(d);
}

// The Monday of the week an ISO date is in.
function mondayOfIso(iso) {
  return addDaysIso(iso, -((parseIsoDate(iso).getDay() + 6) % 7));
}

function formatWeekRange(start, end) {
  return `${formatShortDate(start)} – ${formatShortDate(end)} ${parseIsoDate(end).getFullYear()}`;
}

async function loadWeek() {
  const request = ++weekRequest;
  try {
    const week = await fetch("/api/week" + (weekMonday ? "?week=" + weekMonday : "")).then((r) => r.json());
    if (request !== weekRequest) return;
    weekMonday = week.is_current ? null : week.start;
    renderWeek(week);
  } catch (err) {
    console.error("Could not load the weekly report:", err);
  }
}

function pluralize(count, noun) {
  return count === 1 ? `${count} ${noun}` : `${count} ${noun}s`;
}

// One sentence for the week: what was dictated, what it saved and how it compares with the week before.
// Numbers go through toLocaleString like every other number on the page.
function weekHeadline(week) {
  const words = week.words;
  if (!words) {
    return week.meetings.count
      ? `No dictation, but ${pluralize(week.meetings.count, "meeting")} recorded.`
      : "No dictation in this week.";
  }
  let sentence = `You dictated ${words.toLocaleString()} words on ${pluralize(week.days_dictated, "day")}`;
  if (week.time_saved_minutes >= 1) sentence += `, saving about ${formatSavedMinutes(week.time_saved_minutes)} of typing`;
  sentence += ".";
  if (week.previous_words) {
    const change = Math.round(((words - week.previous_words) * 100) / week.previous_words);
    if (change >= WEEK_SAME_AS_BEFORE_PCT) sentence += ` That is ${change}% more than the week before.`;
    else if (change <= -WEEK_SAME_AS_BEFORE_PCT) sentence += ` That is ${-change}% less than the week before.`;
    else sentence += " That is about the same as the week before.";
  }
  return sentence;
}

// A change against the week before below this share (percent) reads as "about the same".
const WEEK_SAME_AS_BEFORE_PCT = 5;

// Whole minutes, "under 1 min" for a recording shorter than that (a rounded 0 min would read as nothing).
function formatRecordedTime(seconds) {
  return seconds < 60 ? "under 1 min" : formatSavedMinutes(Math.round(seconds / 60));
}

function renderWeekDays(days) {
  const box = document.getElementById("week-days");
  box.replaceChildren();
  box.setAttribute("aria-label", "Words dictated per day: " + days.map((d) => `${d.weekday} ${d.words}`).join(", "));
  const most = Math.max(...days.map((d) => d.words), 1);
  for (const day of days) {
    const col = document.createElement("div");
    col.className = "week-day";
    const value = document.createElement("span");
    value.className = "week-day-value";
    value.textContent = day.words ? day.words.toLocaleString() : "";
    const plot = document.createElement("span");
    plot.className = "week-day-plot";
    const bar = document.createElement("span");
    bar.className = "week-day-bar";
    bar.style.height = (day.words ? Math.max((day.words * 100) / most, 3) : 0) + "%";
    plot.append(bar);
    const name = document.createElement("span");
    name.className = "week-day-name";
    name.textContent = day.weekday;
    col.append(value, plot, name);
    box.append(col);
  }
}

function renderWeek(week) {
  document.getElementById("week-range").textContent = formatWeekRange(week.start, week.end);
  document.getElementById("week-prev").disabled = !week.first_week || week.start <= week.first_week;
  document.getElementById("week-next").disabled = week.is_current;
  document.getElementById("week-current").hidden = week.is_current;
  document.getElementById("week-headline").textContent = weekHeadline(week);
  renderWeekDays(week.days);

  document.getElementById("week-days-dictated").textContent = `${week.days_dictated} of ${week.days_counted}`;
  const meetings = week.meetings;
  document.getElementById("week-meetings").textContent = meetings.count;
  document.getElementById("week-meetings-label").textContent =
    "Meetings" + (meetings.count ? ` (${formatRecordedTime(meetings.seconds)} recorded)` : "");

  const learned = week.learned;
  document.getElementById("week-learned").textContent = learned.total;
  const accepted = learned.suggestions_accepted;
  document.getElementById("week-learned-summary").textContent = accepted
    ? `${pluralize(accepted, "suggestion")} from your edits accepted.`
    : "";
  document.getElementById("week-learned-empty").hidden = learned.total > 0;
  const list = document.getElementById("week-learned-list");
  list.replaceChildren();
  for (const entry of learned.entries) {
    const li = document.createElement("li");
    li.textContent = `${entry.wrong} → ${entry.right}`;
    list.append(li);
  }
  if (learned.entries_total > learned.entries.length) {
    const li = document.createElement("li");
    li.className = "week-learned-more";
    li.textContent = `and ${learned.entries_total - learned.entries.length} more`;
    list.append(li);
  }
}

function showWeek(monday) {
  weekMonday = monday;
  loadWeek();
}

document.getElementById("week-prev").addEventListener("click", () => {
  showWeek(addDaysIso(weekMonday || mondayOfIso(localIsoDate(new Date())), -7));
});
document.getElementById("week-next").addEventListener("click", () => {
  if (weekMonday) showWeek(addDaysIso(weekMonday, 7));
});
document.getElementById("week-current").addEventListener("click", () => showWeek(null));

// ── All charts: period selector ──────────────────────────────────────
document.querySelectorAll(".period-btn[data-period]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".period-btn[data-period]").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentPeriod = btn.dataset.period;
    loadTimeline();
  });
});

document.getElementById("btn-refresh").addEventListener("click", loadAnalytics);
document.getElementById("btn-apply-dates").addEventListener("click", loadAnalytics);
document.getElementById("btn-clear-dates").addEventListener("click", () => {
  document.getElementById("filter-start").value = "";
  document.getElementById("filter-end").value = "";
  loadAnalytics();
});

// ── Analytics Loading ────────────────────────────────────────────────
async function loadAnalytics() {
  const [overview, lang, hours, words, heatmap, models, apps] = await Promise.all([
    api("/api/overview"),
    api("/api/language-distribution"),
    api("/api/peak-usage-hours"),
    api("/api/top-words?n=20"),
    api("/api/activity-heatmap"),
    api("/api/model-breakdown"),
    api("/api/app-breakdown"),
  ]);

  renderOverview(overview);
  renderLanguage(lang);
  renderHours(hours);
  renderWords(words);
  renderHeatmap(heatmap);
  renderModelBreakdown(models);
  renderAppBreakdown(apps);
  loadTimeline();
}

async function loadTimeline() {
  const [timeline, latency, accuracy] = await Promise.all([
    api("/api/transcriptions-over-time?period=" + currentPeriod),
    api("/api/latency-trend?period=" + currentPeriod),
    api("/api/accuracy?period=" + currentPeriod),
  ]);
  renderTimeline(timeline);
  renderLatency(latency);
  renderAccuracy(accuracy);
}

// ── Render Functions ─────────────────────────────────────────────────
function renderOverview(d) {
  document.getElementById("stat-total").textContent = d.total_transcriptions;
  document.getElementById("stat-words").textContent = d.total_words.toLocaleString();
  document.getElementById("stat-wpm").textContent = d.avg_wpm;
  document.getElementById("stat-wpm-label").textContent =
    d.total_transcriptions ? `Avg WPM (${Math.round(d.min_wpm)} to ${Math.round(d.max_wpm)})` : "Avg WPM";
  document.getElementById("stat-duration").textContent = formatDuration(d.avg_duration_ms);
  document.getElementById("stat-longest").textContent = formatDuration(d.longest_duration_ms);
  document.getElementById("stat-longest-label").textContent =
    d.longest_words ? `Longest (${d.longest_words} words)` : "Longest Dictation";
  document.getElementById("stat-transcribe").textContent =
    d.avg_transcribe_ms != null ? formatDuration(d.avg_transcribe_ms) : "--";
  document.getElementById("stat-transcribe-label").textContent =
    d.avg_rtf != null ? `Transcribe (${d.avg_rtf}x real time)` : "Transcribe Time";
  document.getElementById("stat-latency").textContent =
    d.avg_latency_ms != null ? formatDuration(d.avg_latency_ms) : "--";
  document.getElementById("stat-saved").textContent = d.time_saved_minutes;
  document.getElementById("stat-translated").textContent = d.translated_count;
  const parts = [];
  if (d.avg_translate_ms != null) parts.push("avg " + formatDuration(d.avg_translate_ms));
  if (d.translate_failures) parts.push(d.translate_failures + " fell back");
  document.getElementById("stat-translated-label").textContent =
    "Translated" + (parts.length ? " (" + parts.join(", ") + ")" : "");

  document.getElementById("stat-cleaned").textContent = d.cleanup_applied;
  const cleanParts = [];
  if (d.avg_cleanup_ms != null) cleanParts.push("avg " + formatDuration(d.avg_cleanup_ms));
  if (d.cleanup_timeouts) cleanParts.push(d.cleanup_timeouts + " timed out");
  if (d.cleanup_rejected) cleanParts.push(d.cleanup_rejected + " rejected");
  if (d.cleanup_failed) cleanParts.push(d.cleanup_failed + " failed");
  document.getElementById("stat-cleaned-label").textContent =
    "Cleaned Up" + (cleanParts.length ? " (" + cleanParts.join(", ") + ")" : "");

  document.getElementById("stat-polished").textContent = d.polish_applied + d.polish_extension;
  const polishParts = [];
  if (d.avg_polish_ms != null) polishParts.push("avg " + formatDuration(d.avg_polish_ms));
  if (d.polish_timeouts) polishParts.push(d.polish_timeouts + " timed out");
  if (d.polish_failed) polishParts.push(d.polish_failed + " failed");
  document.getElementById("stat-polished-label").textContent =
    "Polished" + (polishParts.length ? " (" + polishParts.join(", ") + ")" : "");

  document.getElementById("stat-commands").textContent = d.command_count;
  document.getElementById("stat-commands-label").textContent =
    "Voice Commands" +
    (d.top_commands.length ? " (" + d.top_commands.map((c) => c.kind + " " + c.count).join(", ") + ")" : "");

  document.getElementById("stat-uploads").textContent = d.upload_count;
  document.getElementById("stat-upload-hours").textContent = d.upload_audio_hours;
  document.getElementById("stat-upload-processing").textContent =
    d.avg_upload_processing_ms != null ? formatDuration(d.avg_upload_processing_ms) : "--";
  document.getElementById("stat-upload-processing-label").textContent =
    d.upload_rtf != null ? `Upload Processing (${d.upload_rtf}x real time)` : "Upload Processing";
  document.getElementById("stat-upload-speakers").textContent =
    d.avg_upload_speakers != null ? d.avg_upload_speakers : "--";
}


function renderTimeline(d) {
  if (charts.timeline) charts.timeline.destroy();
  const ctx = document.getElementById("chart-timeline").getContext("2d");
  charts.timeline = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        borderColor: COLORS.accent,
        backgroundColor: COLORS.accentFill,
        fill: true,
        tension: 0.3,
        pointRadius: d.labels.length > 30 ? 0 : 3,
        pointBackgroundColor: COLORS.accent,
      }],
    },
    options: {
      ...chartDefaults,
      plugins: { ...chartDefaults.plugins, tooltip: { mode: "index", intersect: false } },
    },
  });
}

function renderLanguage(d) {
  if (charts.language) charts.language.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-language").getContext("2d");
  charts.language = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        backgroundColor: [COLORS.accent, COLORS.green, COLORS.orange, COLORS.red, COLORS.accentLight],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: "bottom", labels: { color: COLORS.dim, padding: 16, font: { size: 12 } } },
      },
    },
  });
}

function renderHours(d) {
  if (charts.hours) charts.hours.destroy();
  const ctx = document.getElementById("chart-hours").getContext("2d");
  charts.hours = new Chart(ctx, {
    type: "bar",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        backgroundColor: d.values.map((v) => {
          const max = Math.max(...d.values);
          return v === max ? COLORS.accent : COLORS.accentSoft;
        }),
        borderRadius: 4,
      }],
    },
    options: chartDefaults,
  });
}

function renderWords(d) {
  if (charts.words) charts.words.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-words").getContext("2d");
  charts.words = new Chart(ctx, {
    type: "bar",
    data: {
      labels: d.labels,
      datasets: [{
        data: d.values,
        backgroundColor: COLORS.green,
        borderRadius: 4,
      }],
    },
    options: {
      ...chartDefaults,
      indexAxis: "y",
      scales: {
        x: { ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { color: COLORS.grid }, beginAtZero: true },
        y: { ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

function renderHeatmap(d) {
  const container = document.getElementById("heatmap-container");

  // Show only every 2nd hour label to save space.
  const hourLabels = d.hours.map((h) => (h % 2 === 0 ? String(h).padStart(2, "0") : ""));

  const maxVal = Math.max(...d.grid.flat(), 1);

  let html = '<table class="heatmap-table"><thead><tr><th></th>';
  hourLabels.forEach((h) => { html += "<th>" + h + "</th>"; });
  html += "</tr></thead><tbody>";

  d.days.forEach((day, di) => {
    html += '<tr><td class="day-label">' + day + "</td>";
    d.grid[di].forEach((val) => {
      const intensity = val / maxVal;
      const bg = intensity === 0
        ? withAlpha(COLORS.accent, 0.05)
        : withAlpha(COLORS.accent, 0.15 + intensity * 0.75);
      html += '<td style="background:' + bg + '">' + (val || "") + "</td>";
    });
    html += "</tr>";
  });

  html += "</tbody></table>";
  container.innerHTML = html;
}

// ── Latency Trend Chart ──────────────────────────────────────────────
function renderLatency(d) {
  if (charts.latency) charts.latency.destroy();
  if (!d.labels.length) return;
  const ctx = document.getElementById("chart-latency").getContext("2d");
  const pointRadius = d.labels.length > 30 ? 0 : 3;
  charts.latency = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.labels,
      datasets: [
        {
          label: "Transcribe",
          data: d.transcribe_ms,
          borderColor: COLORS.green,
          backgroundColor: COLORS.greenFill,
          fill: true,
          tension: 0.3,
          pointRadius,
          pointBackgroundColor: COLORS.green,
        },
        {
          label: "Release to text",
          data: d.latency_ms,
          borderColor: COLORS.accent,
          backgroundColor: "transparent",
          tension: 0.3,
          pointRadius,
          pointBackgroundColor: COLORS.accent,
        },
      ],
    },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, ticks: { ...chartDefaults.scales.y.ticks, callback: (v) => formatDuration(v) } },
      },
      plugins: {
        legend: { display: true, labels: { color: COLORS.dim, boxWidth: 12 } },
        tooltip: {
          mode: "index",
          intersect: false,
          callbacks: { label: (c) => c.dataset.label + ": " + (c.parsed.y != null ? formatDuration(c.parsed.y) : "--") },
        },
      },
    },
  });
}

// ── Per-Model Table ──────────────────────────────────────────────────
function renderModelBreakdown(rows) {
  const body = document.getElementById("model-breakdown");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-state">No transcriptions yet.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(
      (m) => `<tr>
        <td>${escapeHtml(m.model)}</td>
        <td>${m.count}</td>
        <td>${m.words.toLocaleString()}</td>
        <td>${m.avg_wpm}</td>
        <td>${formatDuration(m.avg_duration_ms)}</td>
        <td>${m.avg_transcribe_ms != null ? formatDuration(m.avg_transcribe_ms) : "--"}</td>
      </tr>`
    )
    .join("");
}

// ── Per-App Table ────────────────────────────────────────────────────
function renderAppBreakdown(rows) {
  const body = document.getElementById("app-breakdown");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty-state">No transcriptions yet.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(
      (a) => `<tr>
        <td>${escapeHtml(a.app)}</td>
        <td>${a.count}</td>
        <td>${a.words.toLocaleString()}</td>
      </tr>`
    )
    .join("");
}

// ── Accuracy and Dictionary Stats ────────────────────────────────────
function renderAccuracy(d) {
  if (charts.accuracy) charts.accuracy.destroy();
  document.getElementById("accuracy-summary").textContent = d.words
    ? `${d.corrections.toLocaleString()} corrections in ${d.words.toLocaleString()} dictated words. ` +
      "Counted from reviewed transcripts and suggestions you accepted."
    : "";
  if (d.labels.length) {
    const ctx = document.getElementById("chart-accuracy").getContext("2d");
    charts.accuracy = new Chart(ctx, {
      type: "line",
      data: {
        labels: d.labels,
        datasets: [{
          data: d.values,
          borderColor: COLORS.orange,
          backgroundColor: "transparent",
          tension: 0.3,
          pointRadius: d.labels.length > 30 ? 0 : 3,
          pointBackgroundColor: COLORS.orange,
        }],
      },
      options: {
        ...chartDefaults,
        plugins: { ...chartDefaults.plugins, tooltip: { mode: "index", intersect: false } },
      },
    });
  }

  const learn = d.learn;
  document.getElementById("learn-summary").textContent = learn.watched || learn.skipped || learn.accepted
    ? `${learn.watched} watched, ${learn.skipped} skipped. ${learn.suggested} led to suggestions, ${learn.accepted} accepted.`
    : "";
  document.getElementById("learn-outcomes").innerHTML = learn.outcomes.length
    ? learn.outcomes
        .map((o) => `<tr><td>${escapeHtml(o.message)}</td><td>${o.count}</td></tr>`)
        .join("")
    : '<tr><td colspan="2" class="empty-state">No pastes watched yet.</td></tr>';

  document.getElementById("most-corrected").innerHTML = d.most_corrected.length
    ? d.most_corrected
        .map(
          (c) => `<tr>
            <td>${escapeHtml(c.wrong)}</td>
            <td>${escapeHtml(c.right)}</td>
            <td>${c.count}</td>
          </tr>`
        )
        .join("")
    : '<tr><td colspan="3" class="empty-state">No corrections yet.</td></tr>';

  const dict = d.dictionary;
  document.getElementById("dictionary-summary").textContent = dict.items.length
    ? `${dict.entries} entries now. ${dict.total_hits.toLocaleString()} applications in total, removed entries included.`
    : "";
  document.getElementById("dictionary-usage").innerHTML = dict.items.length
    ? dict.items
        .map(
          (e) => `<tr>
            <td>${escapeHtml(e.wrong)}</td>
            <td>${escapeHtml(e.right)}${e.removed ? " (removed)" : ""}</td>
            <td>${e.hits}</td>
            <td>${e.last_used ? escapeHtml(e.last_used) : "--"}</td>
          </tr>`
        )
        .join("")
    : '<tr><td colspan="4" class="empty-state">The dictionary is empty.</td></tr>';
}
