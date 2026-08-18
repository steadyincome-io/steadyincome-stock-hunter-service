// Main application: tab navigation + rendering. All data access goes
// through runQuery()/runQueryOne()/runCount() (sql-loader.js) against the
// in-browser SQLite database -- no network calls after the initial .db fetch.

let currentUniverseRows = [];
let currentSortColumn = "ticker";
let currentSortAsc = true;
let priceChart = null;

async function main() {
  const statusEl = document.getElementById("load-status");
  try {
    await initDatabase();
    statusEl.remove();
    document.getElementById("app").classList.remove("hidden");
  } catch (err) {
    statusEl.textContent = `Failed to load database: ${err.message}`;
    statusEl.classList.add("error");
    return;
  }

  renderSummary();
  renderUniverseTable();
  populateTickerSelect();
  renderPipelineRuns();
  wireTabNav();
  wireUniverseFilters();
  wireTickerSelect();
}

function wireTabNav() {
  document.querySelectorAll(".tab-button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(btn.dataset.tab).classList.add("active");
    });
  });
}

// ---- Summary bar ------------------------------------------------------------

function renderSummary() {
  const activeTickers = runCount(QUERIES.summaryActiveTickers);
  const snapshotRows = runCount(QUERIES.summarySnapshotRows);
  const financialRows = runCount(QUERIES.summaryFinancialRows);
  const financialWithLlm = runCount(QUERIES.summaryFinancialRowsWithLlm);
  const drawdownTickers = runCount(QUERIES.summaryDrawdownTickers);
  const distressTickers = runCount(QUERIES.summaryDistressTickers);
  const eightKEvents = runCount(QUERIES.summaryEightKEvents);
  const latestRun = runQueryOne(QUERIES.summaryLatestRun);

  const cards = [
    ["Active tickers", fmtInt(activeTickers)],
    ["Daily snapshots", fmtInt(snapshotRows)],
    ["SEC filings ingested", fmtInt(financialRows)],
    ["Filings with LLM narrative", fmtInt(financialWithLlm)],
    ["Tickers with drawdown history", fmtInt(drawdownTickers)],
    ["Tickers with distress score", fmtInt(distressTickers)],
    ["8-K debt/bankruptcy events", fmtInt(eightKEvents)],
  ];

  const summaryEl = document.getElementById("summary-cards");
  summaryEl.innerHTML = cards
    .map(([label, value]) => `<div class="metric-card"><div class="metric-value">${value}</div><div class="metric-label">${escapeHtml(label)}</div></div>`)
    .join("");

  const runEl = document.getElementById("latest-run-info");
  if (latestRun) {
    runEl.innerHTML = `Latest pipeline run: <strong>${escapeHtml(latestRun.run_id)}</strong> at ${fmtDate(latestRun.run_timestamp)}
      &middot; ${fmtInt(latestRun.tickers_processed)} tickers &middot; ${fmtNum(latestRun.duration_seconds, 0)}s
      &middot; <span class="badge ${latestRun.status === "SUCCESS" ? "badge-good" : "badge-bad"}">${escapeHtml(latestRun.status)}</span>`;
  } else {
    runEl.textContent = "No pipeline runs recorded yet.";
  }
}

// ---- Universe screener tab -------------------------------------------------

const UNIVERSE_COLUMNS = [
  { key: "ticker", label: "Ticker" },
  { key: "name", label: "Name" },
  { key: "asset_type", label: "Type" },
  { key: "sector", label: "Sector" },
  { key: "market_cap", label: "Mkt Cap ($B)", fmt: (v) => fmtNum(v, 1), numeric: true },
  { key: "price", label: "Price", fmt: (v) => fmtMoney(v), numeric: true },
  { key: "price_change_1d", label: "1D %", fmt: (v) => fmtPct(v), numeric: true },
  { key: "current_drawdown_pct", label: "Curr DD %", fmt: (v) => fmtPct(v), numeric: true },
  { key: "max_drawdown_1y_pct", label: "Max DD 1Y %", fmt: (v) => fmtPct(v), numeric: true },
  { key: "pe_ratio", label: "PE", fmt: (v) => fmtNum(v), numeric: true },
  { key: "dividend_yield_pct", label: "Div Yield %", fmt: (v) => fmtPct(v), numeric: true },
  { key: "quality_score", label: "Quality", fmt: (v) => fmtInt(v), numeric: true, badge: (v) => scoreBadgeClass(v) },
  { key: "investment_score", label: "Investment", fmt: (v) => fmtInt(v), numeric: true, badge: (v) => scoreBadgeClass(v) },
  { key: "risk_score", label: "Risk", fmt: (v) => fmtInt(v), numeric: true, badge: (v) => scoreBadgeClass(v, true) },
  { key: "drawdown_opportunity_score", label: "DD Opp", fmt: (v) => fmtInt(v), numeric: true, badge: (v) => scoreBadgeClass(v) },
  { key: "distress_risk_level", label: "Distress", badge: (v) => riskLevelBadgeClass(v) },
  { key: "valuation_tier", label: "Valuation" },
  { key: "investment_verdict", label: "Verdict" },
  { key: "avg_drawdown_pct", label: "Avg DD %", fmt: (v) => fmtPct(v), numeric: true },
  { key: "worst_drawdown_pct", label: "Worst DD %", fmt: (v) => fmtPct(v), numeric: true },
  { key: "avg_recovery_days", label: "Avg Recovery (d)", fmt: (v) => fmtInt(v), numeric: true },
];

function renderUniverseTable() {
  currentUniverseRows = runQuery(QUERIES.universeTable);
  drawUniverseTable();
}

function applyUniverseFilters(rows) {
  const assetType = document.getElementById("filter-asset-type").value;
  const search = document.getElementById("filter-search").value.trim().toLowerCase();
  const onlyWithSnapshot = document.getElementById("filter-only-snapshot").checked;

  return rows.filter((r) => {
    if (assetType !== "All" && r.asset_type !== assetType) return false;
    if (onlyWithSnapshot && (r.price === null || r.price === undefined)) return false;
    if (search) {
      const hay = `${r.ticker || ""} ${r.name || ""} ${r.sector || ""}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}

function drawUniverseTable() {
  let rows = applyUniverseFilters(currentUniverseRows);

  rows = [...rows].sort((a, b) => {
    const av = a[currentSortColumn];
    const bv = b[currentSortColumn];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    let cmp;
    if (typeof av === "number" && typeof bv === "number") {
      cmp = av - bv;
    } else {
      cmp = String(av).localeCompare(String(bv));
    }
    return currentSortAsc ? cmp : -cmp;
  });

  document.getElementById("universe-count").textContent = `${rows.length} of ${currentUniverseRows.length} active tickers shown.`;

  const thead = UNIVERSE_COLUMNS.map((col) => {
    const sortIndicator = col.key === currentSortColumn ? (currentSortAsc ? " ▲" : " ▼") : "";
    return `<th data-col="${col.key}" class="sortable">${escapeHtml(col.label)}${sortIndicator}</th>`;
  }).join("");

  const tbody = rows
    .map((row) => {
      const cells = UNIVERSE_COLUMNS.map((col) => {
        const raw = row[col.key];
        const display = col.fmt ? col.fmt(raw) : escapeHtml(raw ?? "—");
        if (col.badge) {
          return `<td><span class="badge ${col.badge(raw)}">${display}</span></td>`;
        }
        return `<td>${display}</td>`;
      }).join("");
      return `<tr data-ticker="${escapeHtml(row.ticker)}">${cells}</tr>`;
    })
    .join("");

  document.getElementById("universe-table-head").innerHTML = `<tr>${thead}</tr>`;
  document.getElementById("universe-table-body").innerHTML = tbody;

  document.querySelectorAll("#universe-table-head th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (currentSortColumn === col) {
        currentSortAsc = !currentSortAsc;
      } else {
        currentSortColumn = col;
        currentSortAsc = true;
      }
      drawUniverseTable();
    });
  });

  document.querySelectorAll("#universe-table-body tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      document.getElementById("ticker-select").value = tr.dataset.ticker;
      document.querySelector('.tab-button[data-tab="tab-ticker"]').click();
      renderTickerDetail(tr.dataset.ticker);
    });
  });
}

function wireUniverseFilters() {
  document.getElementById("filter-asset-type").addEventListener("change", drawUniverseTable);
  document.getElementById("filter-search").addEventListener("input", drawUniverseTable);
  document.getElementById("filter-only-snapshot").addEventListener("change", drawUniverseTable);
  document.getElementById("export-csv").addEventListener("click", exportUniverseCsv);
}

function exportUniverseCsv() {
  const rows = applyUniverseFilters(currentUniverseRows);
  const headers = UNIVERSE_COLUMNS.map((c) => c.key);
  const lines = [headers.join(",")];
  rows.forEach((row) => {
    lines.push(headers.map((h) => `"${String(row[h] ?? "").replaceAll('"', '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "universe_screener.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// ---- Ticker detail tab ------------------------------------------------------

function populateTickerSelect() {
  const tickers = runQuery(QUERIES.activeTickers);
  const select = document.getElementById("ticker-select");
  select.innerHTML = tickers
    .map((t) => `<option value="${escapeHtml(t.ticker)}">${escapeHtml(t.ticker)} — ${escapeHtml(t.name || "")}</option>`)
    .join("");
  if (tickers.length) renderTickerDetail(tickers[0].ticker);
}

function wireTickerSelect() {
  document.getElementById("ticker-select").addEventListener("change", (e) => renderTickerDetail(e.target.value));
}

function renderTickerDetail(ticker) {
  const row = runQueryOne(QUERIES.tickerOverview, [ticker]);
  if (!row) {
    document.getElementById("ticker-overview-cards").innerHTML = "<p>No data for this ticker.</p>";
    return;
  }

  document.getElementById("ticker-title").textContent = `${row.ticker} — ${row.name || ""}`;
  document.getElementById("ticker-subtitle").textContent =
    `${row.asset_type || ""} · ${row.sector || ""} · ${row.industry || ""} · Updated ${fmtDate(row.updated_at)}`;

  const cards = [
    ["Price", fmtMoney(row.price)],
    ["1D Change", fmtPct(row.price_change_1d)],
    ["52W High / Low", `${fmtMoney(row.high_52w)} / ${fmtMoney(row.low_52w)}`],
    ["Current Drawdown", fmtPct(row.current_drawdown_pct)],
    ["Max Drawdown (1Y)", fmtPct(row.max_drawdown_1y_pct)],
    ["PE / Forward PE", `${fmtNum(row.pe_ratio)} / ${fmtNum(row.forward_pe)}`],
    ["EV/EBITDA", fmtNum(row.ev_ebitda)],
    ["FCF Yield", fmtPct(row.fcf_yield_pct)],
    ["Dividend Yield", fmtPct(row.dividend_yield_pct)],
    ["Quality Score", fmtInt(row.quality_score)],
    ["Investment Score", fmtInt(row.investment_score)],
    ["Risk Score", fmtInt(row.risk_score)],
    ["Distress Risk", row.distress_risk_level || "—"],
    ["Valuation Tier", row.valuation_tier || "—"],
    ["Verdict", row.investment_verdict || "—"],
    ["Short % of Float", fmtPct(row.short_percent_of_float)],
  ];
  if (row.dcf_fair_value_base !== null && row.dcf_fair_value_base !== undefined) {
    cards.push(["DCF Fair Value (low/base/high)", `${fmtMoney(row.dcf_fair_value_low)} / ${fmtMoney(row.dcf_fair_value_base)} / ${fmtMoney(row.dcf_fair_value_high)}`]);
    cards.push(["DCF Margin of Safety", fmtPct(row.dcf_margin_of_safety_pct)]);
  }

  document.getElementById("ticker-overview-cards").innerHTML = cards
    .map(([label, value]) => `<div class="metric-card small"><div class="metric-value">${escapeHtml(value)}</div><div class="metric-label">${escapeHtml(label)}</div></div>`)
    .join("");

  renderPriceChart(ticker);
  renderDrawdownEvents(ticker);
  renderDistressDetail(ticker);
  renderFilingsTable(ticker);
  renderInsiderTrades(ticker);
  renderEightKEvents(ticker);
  renderEtfHoldings(ticker, row.asset_type);
}

function renderPriceChart(ticker) {
  const rows = runQuery(QUERIES.priceHistory, [ticker, 180]);
  const ctx = document.getElementById("price-chart").getContext("2d");
  if (priceChart) priceChart.destroy();
  if (!rows.length) return;
  priceChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: rows.map((r) => r.trade_date),
      datasets: [{ label: "Close price", data: rows.map((r) => r.close_price), borderColor: "#3b82f6", pointRadius: 0, borderWidth: 1.5 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { x: { ticks: { maxTicksLimit: 8 } } },
      plugins: { legend: { display: false } },
    },
  });
}

function renderDrawdownEvents(ticker) {
  const events = runQuery(QUERIES.drawdownEvents, [ticker, 15]);
  const el = document.getElementById("drawdown-events-table");
  if (!events.length) {
    el.innerHTML = "<p>No drawdown events recorded.</p>";
    return;
  }
  el.innerHTML = renderSimpleTable(events, [
    ["peak_date", "Peak Date"],
    ["peak_price", "Peak Price", (v) => fmtMoney(v)],
    ["bottom_date", "Bottom Date"],
    ["bottom_price", "Bottom Price", (v) => fmtMoney(v)],
    ["drawdown_pct", "Drawdown %", (v) => fmtPct(v)],
    ["recovery_date", "Recovery Date"],
    ["days_to_bottom", "Days to Bottom", (v) => fmtInt(v)],
    ["recovery_duration_days", "Recovery (days)", (v) => fmtInt(v)],
    ["is_ongoing", "Ongoing?", (v) => (v ? "Yes" : "No")],
  ]);
}

function renderDistressDetail(ticker) {
  const row = runQueryOne(QUERIES.distressScore, [ticker]);
  const el = document.getElementById("distress-detail");
  if (!row) {
    el.innerHTML = "<p>No distress score computed for this ticker.</p>";
    return;
  }
  el.innerHTML = `
    <div class="metric-card small"><div class="metric-value">${fmtInt(row.distress_risk_score)}</div><div class="metric-label">Distress Risk Score</div></div>
    <div class="metric-card small"><div class="metric-value"><span class="badge ${riskLevelBadgeClass(row.risk_level)}">${escapeHtml(row.risk_level || "—")}</span></div><div class="metric-label">Risk Level</div></div>
    <div class="metric-card small"><div class="metric-value">${fmtNum(row.altman_z)}</div><div class="metric-label">Altman Z-Score</div></div>
    <div class="metric-card small"><div class="metric-value">${fmtInt(row.piotroski_f)}</div><div class="metric-label">Piotroski F-Score</div></div>
  `;
}

function renderFilingsTable(ticker) {
  const filings = runQuery(QUERIES.tickerFilings, [ticker]);
  const el = document.getElementById("filings-table");
  if (!filings.length) {
    el.innerHTML = "<p>No SEC filings ingested for this ticker.</p>";
    return;
  }
  el.innerHTML = renderSimpleTable(filings, [
    ["filing_date", "Filing Date"],
    ["form_type", "Form"],
    ["revenue_usd", "Revenue", (v) => fmtMoneyAuto(v)],
    ["net_income_usd", "Net Income", (v) => fmtMoneyAuto(v)],
    ["eps_diluted", "EPS (diluted)", (v) => fmtMoney(v)],
    ["debt_to_equity_ratio", "Debt/Equity", (v) => fmtNum(v)],
    ["risk_score", "Risk Score", (v) => fmtInt(v)],
    ["md_a_summary", "MD&A Summary"],
    ["risk_summary", "Risk Summary"],
  ]);
}

function renderInsiderTrades(ticker) {
  const trades = runQuery(QUERIES.insiderTrades, [ticker, 30]);
  const el = document.getElementById("insider-trades-table");
  if (!trades.length) {
    el.innerHTML = "<p>No insider trades recorded.</p>";
    return;
  }
  el.innerHTML = renderSimpleTable(trades, [
    ["trade_date", "Trade Date"],
    ["insider_name", "Insider"],
    ["title", "Title"],
    ["code", "Code"],
    ["shares", "Shares", (v) => fmtInt(v)],
    ["price_per_share", "Price/Share", (v) => fmtMoney(v)],
    ["total_value", "Total Value", (v) => fmtMoneyAuto(v)],
    ["sentiment", "Sentiment"],
  ]);
}

function renderEightKEvents(ticker) {
  const events = runQuery(QUERIES.eightKEvents, [ticker, 15]);
  const el = document.getElementById("eightk-table");
  if (!events.length) {
    el.innerHTML = "<p>No 8-K debt/bankruptcy events recorded.</p>";
    return;
  }
  el.innerHTML = renderSimpleTable(events, [
    ["filing_date", "Filing Date"],
    ["item_codes", "Item Codes"],
    ["is_debt_related", "Debt-related?", (v) => (v ? "Yes" : "No")],
    ["is_bankruptcy_related", "Bankruptcy-related?", (v) => (v ? "Yes" : "No")],
    ["description", "Description"],
  ]);
}

function renderEtfHoldings(ticker, assetType) {
  const section = document.getElementById("etf-holdings-section");
  if (assetType !== "ETF") {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const latest = runQueryOne(QUERIES.etfLatestFilingDate, [ticker]);
  const el = document.getElementById("etf-holdings-table");
  if (!latest || !latest.filing_date) {
    el.innerHTML = "<p>No holdings data ingested for this ETF.</p>";
    return;
  }
  const holdings = runQuery(QUERIES.etfHoldings, [ticker, latest.filing_date, 25]);
  document.getElementById("etf-holdings-caption").textContent = `As of ${latest.filing_date}`;
  el.innerHTML = renderSimpleTable(holdings, [
    ["holding_name", "Holding"],
    ["holding_ticker", "Ticker"],
    ["weight_pct", "Weight %", (v) => fmtPct(v)],
    ["market_value", "Market Value", (v) => fmtMoneyAuto(v)],
    ["country", "Country"],
  ]);
}

function renderSimpleTable(rows, columns) {
  const thead = columns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("");
  const tbody = rows
    .map((row) => {
      const cells = columns
        .map(([key, , fmt]) => `<td>${fmt ? fmt(row[key]) : escapeHtml(row[key] ?? "—")}</td>`)
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table class="data-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
}

// ---- Run history tab --------------------------------------------------------

function renderPipelineRuns() {
  const runs = runQuery(QUERIES.pipelineRuns, [50]);
  const el = document.getElementById("pipeline-runs-table");
  if (!runs.length) {
    el.innerHTML = "<p>No pipeline runs recorded.</p>";
    return;
  }
  el.innerHTML = renderSimpleTable(runs, [
    ["run_id", "Run ID"],
    ["run_timestamp", "Timestamp", (v) => fmtDate(v)],
    ["duration_seconds", "Duration (s)", (v) => fmtNum(v, 0)],
    ["tickers_processed", "Tickers", (v) => fmtInt(v)],
    ["status", "Status", (v) => `<span class="badge ${v === "SUCCESS" ? "badge-good" : "badge-bad"}">${escapeHtml(v)}</span>`],
  ]);
}

document.addEventListener("DOMContentLoaded", main);
