/* NexusVox Dashboard — Dev tab: benchmarks */

// ── Benchmarks (Dev Tab) ─────────────────────────────────────────────

let benchmarkCache = null;
let currentBenchmarkDetail = null;

async function loadBenchmarks() {
  await featuresReady;
  if (!FEATURES.dev_tab) return;
  try {
    const comparison = await fetch("/api/benchmarks/compare").then((r) => r.json());
    benchmarkCache = comparison;
    populateBenchmarkSelector(comparison.benchmarks);
    showComparison(comparison);
  } catch (err) {
    console.error("Failed to load benchmarks:", err);
  }
}

function populateBenchmarkSelector(benchmarks) {
  const sel = document.getElementById("dev-benchmark-select");
  // Keep the first default option, remove the rest
  while (sel.options.length > 1) sel.remove(1);
  benchmarks.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b.filename;
    opt.textContent = `${b.model_key} — ${b.lang.replace("_", " ").toUpperCase()} (WER ${(b.wer_mean * 100).toFixed(1)}%)`;
    sel.appendChild(opt);
  });
}

// Wire up selector & compare-all button
document.getElementById("dev-benchmark-select").addEventListener("change", async (e) => {
  if (!e.target.value) {
    showComparison(benchmarkCache);
    return;
  }
  await loadBenchmarkDetail(e.target.value);
});

document.getElementById("dev-view-compare").addEventListener("click", () => {
  document.getElementById("dev-benchmark-select").value = "";
  showComparison(benchmarkCache);
});

// ── Comparison View ──────────────────────────────────────────────────

function showComparison(comparison) {
  if (!comparison || !comparison.benchmarks.length) {
    document.getElementById("dev-empty").style.display = "";
    document.getElementById("dev-content").style.display = "none";
    return;
  }
  document.getElementById("dev-empty").style.display = "none";
  document.getElementById("dev-content").style.display = "";
  document.getElementById("dev-comparison").style.display = "";
  document.getElementById("dev-detail").style.display = "none";

  renderComparisonStats(comparison.benchmarks);
  renderComparisonTable(comparison);
  renderWerChart(comparison);
  renderRtfChart(comparison);
}

function renderComparisonStats(benchmarks) {
  const totalSamples = benchmarks.reduce((s, b) => s + b.total_samples, 0);
  const totalAudio = benchmarks.reduce((s, b) => s + b.total_audio_duration_s, 0);
  const bestWer = Math.min(...benchmarks.map((b) => b.wer_mean));
  const bestRtf = Math.min(...benchmarks.map((b) => b.rtf));

  document.getElementById("dev-stats-row").innerHTML = `
    <div class="stat-card"><span class="stat-value">${benchmarks.length}</span><span class="stat-label">Benchmarks</span></div>
    <div class="stat-card"><span class="stat-value">${totalSamples.toLocaleString()}</span><span class="stat-label">Total Samples</span></div>
    <div class="stat-card"><span class="stat-value">${(totalAudio / 60).toFixed(0)} min</span><span class="stat-label">Total Audio</span></div>
    <div class="stat-card"><span class="stat-value">${(bestWer * 100).toFixed(1)}%</span><span class="stat-label">Best WER</span></div>
    <div class="stat-card"><span class="stat-value">${bestRtf.toFixed(4)}</span><span class="stat-label">Best RTF</span></div>
  `;
}

function renderComparisonTable(comparison) {
  const container = document.getElementById("dev-comparison-table-container");
  const rows = comparison.benchmarks
    .map((b) => {
      const badge = b.passes_target
        ? `<span class="pass-badge">Pass</span>`
        : `<span class="fail-badge">Fail</span>`;
      const targetPct = b.wer_target != null ? (b.wer_target * 100).toFixed(0) + "%" : "—";
      return `<tr>
        <td>${escapeHtml(b.model_key)}</td>
        <td>${b.lang.replace("_", " ").toUpperCase()}</td>
        <td class="num">${(b.wer_mean * 100).toFixed(2)}%</td>
        <td class="num">${(b.wer_median * 100).toFixed(2)}%</td>
        <td class="num">${(b.wer_p90 * 100).toFixed(2)}%</td>
        <td class="num">${(b.wer_stddev * 100).toFixed(2)}%</td>
        <td class="num">${targetPct}</td>
        <td>${badge}</td>
        <td class="num">${b.rtf.toFixed(4)}</td>
        <td class="num">${b.total_samples}</td>
        <td>${b.timestamp ? new Date(b.timestamp).toLocaleDateString() : "—"}</td>
      </tr>`;
    })
    .join("");

  container.innerHTML = `
    <table class="benchmark-table">
      <thead><tr>
        <th>Model</th><th>Language</th><th>WER Mean</th><th>WER Median</th><th>WER P90</th><th>WER Std</th><th>Target</th><th>Status</th><th>RTF</th><th>Samples</th><th>Date</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderWerChart(comparison) {
  const ctx = document.getElementById("chart-dev-wer");
  if (charts.devWer) charts.devWer.destroy();

  const labels = comparison.benchmarks.map((b) => `${b.model_key}\n${b.lang.replace("_", " ").toUpperCase()}`);
  const werData = comparison.benchmarks.map((b) => +(b.wer_mean * 100).toFixed(2));
  const bgColors = comparison.benchmarks.map((b) => (b.passes_target ? COLORS.green : COLORS.orange));

  // Build annotation lines for targets
  const annotations = {};
  const targetEntries = Object.entries(comparison.targets);
  const targetColors = [COLORS.accent, COLORS.orange];
  targetEntries.forEach(([lang, val], i) => {
    annotations["target_" + lang] = {
      type: "line",
      yMin: val * 100,
      yMax: val * 100,
      borderColor: targetColors[i] || COLORS.dim,
      borderDash: [6, 4],
      borderWidth: 2,
      label: {
        display: true,
        content: `Target ${lang.replace("_", " ").toUpperCase()} (${(val * 100).toFixed(0)}%)`,
        position: "end",
        color: COLORS.text,
        font: { size: 10 },
        backgroundColor: "rgba(0,0,0,0.5)",
      },
    };
  });

  charts.devWer = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ data: werData, backgroundColor: bgColors, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      plugins: {
        legend: { display: false },
        annotation: { annotations },
      },
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: { display: true, text: "WER %", color: COLORS.dim } },
      },
    },
  });
}

function renderRtfChart(comparison) {
  const ctx = document.getElementById("chart-dev-rtf");
  if (charts.devRtf) charts.devRtf.destroy();

  const labels = comparison.benchmarks.map((b) => `${b.model_key}\n${b.lang.replace("_", " ").toUpperCase()}`);
  const rtfData = comparison.benchmarks.map((b) => +b.rtf.toFixed(4));

  charts.devRtf = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ data: rtfData, backgroundColor: COLORS.accent, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      indexAxis: "y",
      scales: {
        x: { ...chartDefaults.scales.x, title: { display: true, text: "RTF (lower = faster)", color: COLORS.dim } },
        y: { ...chartDefaults.scales.y, ticks: { color: COLORS.dim, font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

// ── Detail View (single benchmark) ───────────────────────────────────

async function loadBenchmarkDetail(filename) {
  try {
    const data = await fetch(`/api/benchmarks/${encodeURIComponent(filename)}`).then((r) => r.json());
    if (data.error) {
      console.error(data.error);
      return;
    }
    currentBenchmarkDetail = data;
    showDetail(data);
  } catch (err) {
    console.error("Failed to load benchmark detail:", err);
  }
}

function showDetail(data) {
  document.getElementById("dev-comparison").style.display = "none";
  document.getElementById("dev-detail").style.display = "";

  const s = data.summary;
  document.getElementById("dev-detail-stats").innerHTML = `
    <div class="stat-card"><span class="stat-value">${escapeHtml(s.model_key)}</span><span class="stat-label">Model</span></div>
    <div class="stat-card"><span class="stat-value">${s.lang.replace("_", " ").toUpperCase()}</span><span class="stat-label">Language</span></div>
    <div class="stat-card"><span class="stat-value">${(s.wer_mean * 100).toFixed(2)}%</span><span class="stat-label">WER Mean</span></div>
    <div class="stat-card"><span class="stat-value">${(s.wer_median * 100).toFixed(2)}%</span><span class="stat-label">WER Median</span></div>
    <div class="stat-card"><span class="stat-value">${(s.wer_p90 * 100).toFixed(2)}%</span><span class="stat-label">WER P90</span></div>
    <div class="stat-card"><span class="stat-value">${s.rtf.toFixed(4)}</span><span class="stat-label">RTF</span></div>
    <div class="stat-card"><span class="stat-value">${s.total_samples}</span><span class="stat-label">Samples</span></div>
    <div class="stat-card"><span class="stat-value">${s.failed_samples}</span><span class="stat-label">Failed</span></div>
  `;

  renderWerDistribution(data.samples);
  renderLatencyScatter(data.samples);
  renderSampleExplorer(data.samples);
}

function renderWerDistribution(samples) {
  const ctx = document.getElementById("chart-dev-wer-dist");
  if (charts.devWerDist) charts.devWerDist.destroy();

  // Bucket samples by WER %
  const buckets = [
    { label: "0%", min: 0, max: 0.001 },
    { label: "0-5%", min: 0.001, max: 0.05 },
    { label: "5-10%", min: 0.05, max: 0.10 },
    { label: "10-20%", min: 0.10, max: 0.20 },
    { label: "20-50%", min: 0.20, max: 0.50 },
    { label: "50%+", min: 0.50, max: Infinity },
  ];

  const counts = buckets.map((b) => samples.filter((s) => s.wer >= b.min && s.wer < b.max).length);
  const colors = [COLORS.green, COLORS.green, COLORS.accent, COLORS.orange, COLORS.orange, COLORS.red];

  charts.devWerDist = new Chart(ctx, {
    type: "bar",
    data: {
      labels: buckets.map((b) => b.label),
      datasets: [{ data: counts, backgroundColor: colors, borderRadius: 4 }],
    },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        x: { ...chartDefaults.scales.x, title: { display: true, text: "WER Range", color: COLORS.dim } },
        y: { ...chartDefaults.scales.y, title: { display: true, text: "Samples", color: COLORS.dim } },
      },
    },
  });
}

function renderLatencyScatter(samples) {
  const ctx = document.getElementById("chart-dev-latency");
  if (charts.devLatency) charts.devLatency.destroy();

  const points = samples
    .filter((s) => s.latency_s != null && s.audio_duration_s != null)
    .map((s) => ({ x: s.audio_duration_s, y: s.latency_s }));

  charts.devLatency = new Chart(ctx, {
    type: "scatter",
    data: {
      datasets: [
        {
          data: points,
          backgroundColor: COLORS.accent + "88",
          borderColor: COLORS.accent,
          pointRadius: 3,
          pointHoverRadius: 5,
        },
      ],
    },
    options: {
      ...chartDefaults,
      scales: {
        x: {
          ...chartDefaults.scales.x,
          title: { display: true, text: "Audio Duration (s)", color: COLORS.dim },
          beginAtZero: true,
        },
        y: {
          ...chartDefaults.scales.y,
          title: { display: true, text: "Latency (s)", color: COLORS.dim },
        },
      },
    },
  });
}

// ── Sample Explorer ──────────────────────────────────────────────────

function renderSampleExplorer(samples) {
  const container = document.getElementById("dev-sample-list");
  const countEl = document.getElementById("dev-sample-count");
  const werFilter = document.getElementById("dev-wer-filter");
  const searchFilter = document.getElementById("dev-sample-search");

  function render() {
    const pct = parseFloat(werFilter.value); // a Max WER of 0 shows only perfect samples; empty means no limit (100%)
    const maxWer = Number.isNaN(pct) ? 1 : pct / 100;
    const query = (searchFilter.value || "").toLowerCase();

    const filtered = samples.filter((s) => {
      if (s.wer > maxWer) return false;
      if (query && !s.reference.toLowerCase().includes(query) && !s.hypothesis.toLowerCase().includes(query)) return false;
      return true;
    });

    // Sort by WER descending (worst first)
    filtered.sort((a, b) => b.wer - a.wer);

    countEl.textContent = `${filtered.length} / ${samples.length} samples`;

    // Limit rendering to 100 samples for performance
    const shown = filtered.slice(0, 100);

    container.innerHTML = shown
      .map((s) => {
        const werPct = (s.wer * 100).toFixed(1);
        const werClass = s.wer <= 0.05 ? "good" : s.wer <= 0.15 ? "mid" : "bad";
        const diffHtml = wordDiff(s.reference, s.hypothesis);
        return `
        <div class="sample-card">
          <div class="sample-card-header">
            <span class="sample-wer ${werClass}">${werPct}% WER</span>
            <span>#${s.index}</span>
            <span>ID: ${escapeHtml(String(s.id))}</span>
            <span>${s.audio_duration_s != null ? s.audio_duration_s.toFixed(1) + "s audio" : ""}</span>
            <span>${s.latency_s != null ? s.latency_s.toFixed(3) + "s latency" : ""}</span>
          </div>
          <div class="sample-text"><span class="label">Ref</span>${escapeHtml(s.reference)}</div>
          <div class="sample-text"><span class="label">Hyp</span>${diffHtml}</div>
        </div>`;
      })
      .join("");

    if (filtered.length > 100) {
      container.innerHTML += `<div class="empty-state"><p>Showing first 100 of ${filtered.length} samples. Narrow the filter to see more.</p></div>`;
    }
  }

  // Assigned, not added: opening another benchmark replaces the handler instead of stacking one per field.
  werFilter.oninput = render;
  searchFilter.oninput = render;
  render();
}

/**
 * Simple word-level diff: marks added/deleted words between reference and hypothesis.
 */
function wordDiff(ref, hyp) {
  const refWords = ref.split(/\s+/);
  const hypWords = hyp.split(/\s+/);

  // LCS-based diff for reasonable accuracy
  const m = refWords.length;
  const n = hypWords.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = refWords[i - 1] === hypWords[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Backtrack
  const parts = [];
  let i = m,
    j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && refWords[i - 1] === hypWords[j - 1]) {
      parts.push(escapeHtml(hypWords[j - 1]));
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      parts.push(`<span class="diff-add">${escapeHtml(hypWords[j - 1])}</span>`);
      j--;
    } else {
      parts.push(`<span class="diff-del">${escapeHtml(refWords[i - 1])}</span>`);
      i--;
    }
  }

  return parts.reverse().join(" ");
}
