/* NexusVox Dashboard — Analytics tab: period selector, charts, tables */

// ── Period Selector ──────────────────────────────────────────────────
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
