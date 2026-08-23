import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fetchOptionsChain } from "../lib/optionsApi";
import { fmtPrice, fmtPct, fmtNum, fmtDate, fmtCompact, changeColorClass, riskLevelChip, verdictChip } from "../lib/format";

const RATE_LIMIT_RETRY_MS = 8000;

const OPTIONS_COLUMN_COUNT = 6;

// Where among the (strike-sorted) rows the current price falls -- the red
// line renders just before the first row whose strike is >= price, or after
// the last row if price is above every strike in this table.
function priceLineIndex(rows, price) {
  if (price == null) return null;
  const idx = rows.findIndex((r) => r.strike >= price);
  return idx === -1 ? rows.length : idx;
}

function PriceLineRow({ price }) {
  return (
    <tr aria-label={`Current price ${price}`}>
      <td colSpan={OPTIONS_COLUMN_COUNT} className="p-0">
        <div className="relative border-t-2 border-danger my-1">
          <span className="absolute -top-2.5 right-1 bg-surface-container-lowest px-1 text-[10px] leading-none text-danger font-medium">
            Current: {fmtPrice(price)}
          </span>
        </div>
      </td>
    </tr>
  );
}

function OptionsTable({ title, rows, price }) {
  const lineIdx = priceLineIndex(rows, price);
  return (
    <div>
      <h4 className="text-label-mono text-on-surface-variant uppercase mb-2">{title}</h4>
      {rows.length === 0 ? (
        <p className="text-on-surface-variant text-body-sm">No contracts at this expiration.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                <th className="py-cell-padding-y px-cell-padding-x font-normal">Strike</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Bid</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Ask</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Last</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">IV</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Delta</th>
              </tr>
            </thead>
            <tbody className="text-data-tabular">
              {rows.map((r, i) => (
                <Fragment key={r.symbol}>
                  {lineIdx === i && <PriceLineRow price={price} />}
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x">{fmtPrice(r.strike)}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{r.bid != null ? fmtPrice(r.bid) : "--"}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{r.ask != null ? fmtPrice(r.ask) : "--"}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{r.last != null ? fmtPrice(r.last) : "--"}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{r.implied_volatility != null ? fmtPct(r.implied_volatility * 100) : "--"}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{r.delta != null ? fmtNum(r.delta, 3) : "--"}</td>
                  </tr>
                </Fragment>
              ))}
              {lineIdx === rows.length && <PriceLineRow price={price} />}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function OptionsChain({ ticker, price }) {
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const retryTimer = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function load() {
      setState({ status: "loading", data: null, error: null });
      try {
        const data = await fetchOptionsChain(ticker, price, { signal: controller.signal });
        if (!cancelled) setState({ status: "ready", data, error: null });
      } catch (err) {
        if (cancelled || err.name === "AbortError") return;
        if (err.rateLimited) {
          setState({ status: "rate_limited", data: null, error: err });
          retryTimer.current = setTimeout(load, RATE_LIMIT_RETRY_MS);
        } else {
          setState({ status: "error", data: null, error: err });
        }
      }
    }

    load();
    return () => {
      cancelled = true;
      controller.abort();
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, [ticker, price]);

  if (state.status === "loading") {
    return (
      <div className="p-6 flex flex-col items-center text-center gap-2">
        <span className="material-symbols-outlined text-outline text-3xl animate-spin">progress_activity</span>
        <p className="text-on-surface-variant text-body-sm">Fetching {ticker}'s options chain (next 21 days, strikes within $20 of price) from Alpaca...</p>
      </div>
    );
  }

  if (state.status === "rate_limited") {
    return (
      <div className="p-6 flex flex-col items-center text-center gap-2">
        <span className="material-symbols-outlined text-[#d97706] text-3xl animate-spin">progress_activity</span>
        <p className="text-on-surface-variant text-body-sm max-w-md">
          Alpaca's free-tier rate limit ({state.error.limitPerMin || 200}/min) was reached. Retrying automatically
          every {RATE_LIMIT_RETRY_MS / 1000}s...
        </p>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="p-6 flex flex-col items-center text-center gap-2">
        <span className="material-symbols-outlined text-danger text-3xl">error</span>
        <p className="text-on-surface-variant text-body-sm max-w-md">
          Couldn't reach the options API ({state.error.message}). Is it running? Start it with{" "}
          <code className="font-mono text-on-surface">./scripts/dev_start.sh</code>.
        </p>
      </div>
    );
  }

  const { data } = state;
  if (!data.expirations || data.expirations.length === 0) {
    return (
      <div className="p-6 flex flex-col items-center text-center gap-2">
        <span className="material-symbols-outlined text-outline text-3xl">info</span>
        <p className="text-on-surface-variant text-body-sm">No option contracts found for {ticker} within the next 21 days / $20 of price.</p>
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-8">
      <p className="text-body-sm text-on-surface-variant">
        All expirations through 21 days out, strikes within $20 of the current price ({fmtPrice(price)}) --
        via Alpaca's indicative feed, delayed quotes.
      </p>
      {data.expirations.map((exp) => (
        <div key={exp.expiration_date} className="border-t border-surface-container pt-6 first:border-t-0 first:pt-0">
          <h4 className="text-body-md font-medium text-primary mb-3">
            {exp.expiration_date} <span className="text-on-surface-variant font-normal">({exp.days_to_expiration}d out)</span>
          </h4>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <OptionsTable title="Calls" rows={exp.calls} price={price} />
            <OptionsTable title="Puts" rows={exp.puts} price={price} />
          </div>
        </div>
      ))}
    </div>
  );
}

// Definitions kept in sync with the actual calculation code (scoring.py,
// distress_analytics.py, pipeline.py) rather than generic textbook wording,
// so the tooltip always matches what this pipeline actually computes.
//
// Quality/Investment/Risk Score all branch by asset_type in pipeline.py:
// - Quality Score for a Stock uses scoring.compute_quality_score (fundamentals-
//   based). For an ETF it instead uses the separate, simpler rule-based
//   calculate_quality_score() (expense ratio / turnover / drawdown), since
//   ETFs don't file 10-Ks and have none of compute_quality_score's inputs.
// - Risk Score always runs through scoring.compute_risk_score, but for ETFs
//   almost every input (distress model, insider sentiment, 8-K flags, LLM
//   filing risk) is unavailable and falls back to a neutral 50 -- only the
//   real drawdown-severity term reflects actual ETF data. Confirmed live
//   against SPY: risk_score=45 is 0.90x50 (six neutral defaults) + 0.10x3
//   (real drawdown term).
// - Investment Score's formula is the same either way, but an ETF's "quality"
//   and "valuation" inputs are literally the same legacy ETF score reused
//   for both slots.
const STAT_INFO = {
  lastPrice: "Most recent close from the pipeline's daily yfinance snapshot. The percentage is the 1-day change vs. the prior close.",
  range52w: "Lowest and highest daily close over the trailing 252 trading days (~1 year) from price_history, recomputed on every pipeline run. The marker shows where the current price sits in that range.",
  peRatio: "Price divided by trailing twelve-month earnings per share.",
  divYield: "Annualized dividend per share divided by the current price.",
  qualityScore: (isETF) =>
    isETF
      ? "0-100 rule-based ETF score (pipeline.calculate_quality_score): starts at 70, +10/-10 for a low/high expense ratio, +5/-5 for low/high turnover, +10/+5 if the current drawdown is worse than -20%/-10%. Clamped to [10, 99]. Does NOT use the fundamentals-based formula stocks get -- ETFs have no 10-K data to feed it."
      : "0-100 composite (scoring.compute_quality_score): 15% revenue-growth stability, 15% net margin, 20% free-cash-flow margin, 15% operating margin, 15% balance-sheet strength (debt/equity), 10% return on assets, 10% dividend/buyback yield. Higher = higher quality.",
  investmentScore: (isETF) =>
    isETF
      ? "0-100 blend (scoring.compute_investment_score): 30% Quality Score, 20% valuation score (for ETFs, the SAME legacy ETF score as Quality Score, reused), 15% inverse of Risk Score, 10% inverse of filing-derived risk (defaults to Risk Score itself, since ETFs have no filing-risk score), 10% drawdown opportunity, 10% dividend score, 5% insider sentiment (fixed at 50 -- ETFs have no insider trades). Higher = stronger overall case."
      : "0-100 blend (scoring.compute_investment_score): 30% Quality Score, 20% valuation, 15% inverse of Risk Score, 10% inverse of filing-derived risk, 10% drawdown opportunity, 10% dividend score, 5% insider sentiment. Higher = stronger overall case.",
  riskScore: (isETF) =>
    isETF
      ? "0-100 composite (scoring.compute_risk_score), but for ETFs most inputs are unavailable and default to a neutral 50: balance-sheet/distress risk (no 10-K), liquidity, earnings stability, cash-flow risk, LLM filing risk (no filings to score), legal sentiment, and insider-selling risk (no insider trades) are all 50. Drawdown severity (10% weight) reflects real ETF data, and the score is further nudged down by up to 10 points based on the fund's real N-PORT cash position (pipeline.py: -min(cash_pct x 2, 10)) -- more cash cushion means less forced-selling/liquidity risk. Still mostly a neutral baseline, not a full distress assessment."
      : "0-100 composite (scoring.compute_risk_score): 20% balance-sheet/distress risk, 15% liquidity, 10% earnings stability, 15% cash-flow risk, 15% LLM-derived filing risk, 10% legal sentiment, 10% drawdown severity, 5% insider-selling signal. A bankruptcy-related 8-K forces the score to at least 85; a debt-covenant 8-K adds +8. Higher = riskier.",
  revenue: "Total revenue reported in the ticker's most recent 10-K, from SEC XBRL data.",
  netIncome: "Net income reported in the same 10-K filing.",
  fcfYield: "Free cash flow divided by market cap, from yfinance fundamentals.",
  debtToEquity: "Total debt divided by stockholders' equity, both from the latest 10-K's XBRL data.",
  altmanZ: (isETF) =>
    "Classic 5-factor Altman Z-Score (distress_analytics.compute_altman_z): 1.2x(working capital/assets) + 1.4x(retained earnings/assets) + 3.3x(operating income/assets) + 0.6x(market cap/total liabilities) + 1.0x(revenue/assets). Needs at least 3 of the 5 inputs. Zones: >2.99 safe, 1.81-2.99 grey, <1.81 distress." +
    (isETF ? " Stocks only -- ETFs don't file 10-Ks, so this is never computed for them." : ""),
  piotroski: (isETF) =>
    "9-point checklist (distress_analytics.compute_piotroski_f) comparing the latest 10-K to the prior year: positive ROA, positive operating cash flow, improving ROA, cash flow exceeding net income, falling leverage, improving current ratio, no meaningful new share issuance, improving operating margin, improving asset turnover. Each met criterion scores 1 point; criteria that can't be evaluated from available data are skipped and the score is scaled to /9." +
    (isETF ? " Stocks only -- ETFs don't file 10-Ks, so this is never computed for them." : ""),
};

function statText(key, isETF) {
  const entry = STAT_INFO[key];
  return typeof entry === "function" ? entry(isETF) : entry;
}

function InfoTip({ text }) {
  if (!text) return null;
  return (
    <span className="relative inline-flex group ml-1 align-middle">
      <span className="material-symbols-outlined text-outline text-sm cursor-help">info</span>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-2 w-64 -translate-x-1/2 rounded-lg border border-outline-variant bg-surface-container-lowest p-3 text-body-sm normal-case leading-snug text-on-surface opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

function ScoreGauge({ title, value, max, label, labelCls, barCls, unit, tooltip }) {
  const pct = value != null ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-6 flex flex-col items-center">
      <h3 className="text-headline-sm text-primary mb-6 w-full text-left border-b border-surface-container pb-2 flex items-center">
        {title}
        <InfoTip text={tooltip} />
      </h3>
      <div className="w-full mb-6">
        <div className="flex justify-between items-center mb-2">
          <span className="text-label-mono text-outline uppercase">{unit}</span>
          <span className="text-label-mono text-primary">{value != null ? `${pct.toFixed(0)}%` : "--"}</span>
        </div>
        <div className="relative h-3 bg-surface-container rounded-full overflow-hidden">
          <div className={`absolute top-0 left-0 h-full rounded-full ${barCls}`} style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="text-center">
        <span className="text-display-lg text-primary leading-none block">{value != null ? fmtNum(value, 0) : "--"}</span>
        {label && <span className={`text-label-mono px-2 py-1 rounded mt-2 inline-block ${labelCls}`}>{label}</span>}
      </div>
    </div>
  );
}

function scoreLabel(score) {
  if (score == null) return { label: "Unscored", cls: "text-on-surface-variant bg-outline-variant/20" };
  if (score >= 80) return { label: "Exceptional", cls: "text-[#16a34a] bg-[#16a34a]/10" };
  if (score >= 60) return { label: "Strong", cls: "text-[#16a34a] bg-[#16a34a]/10" };
  if (score >= 40) return { label: "Moderate", cls: "text-[#d97706] bg-[#d97706]/10" };
  return { label: "Weak", cls: "text-danger bg-danger/10" };
}

function riskScoreLabel(score) {
  if (score == null) return { label: "Unscored", cls: "text-on-surface-variant bg-outline-variant/20" };
  if (score < 40) return { label: "Low Risk", cls: "text-[#16a34a] bg-[#16a34a]/10" };
  if (score < 65) return { label: "Moderate Risk", cls: "text-[#d97706] bg-[#d97706]/10" };
  return { label: "High Risk", cls: "text-danger bg-danger/10" };
}

export default function TickerAnalysis() {
  const params = useParams();
  const navigate = useNavigate();
  const { db, loading, error, query, queryOne } = useDatabase();
  const [pickerValue, setPickerValue] = useState("");

  const tickers = useMemo(() => (db ? query(QUERIES.activeTickers) : []), [db]);
  const ticker = params.ticker;

  const detail = useMemo(() => {
    if (!db || !ticker) return null;
    return {
      overview: queryOne(QUERIES.tickerOverview, [ticker]),
      distress: queryOne(QUERIES.distressScore, [ticker]),
      filings: query(QUERIES.tickerFilings, [ticker]),
      insiders: query(QUERIES.insiderTradesForTicker, [ticker, 10]),
      drawdowns: query(QUERIES.drawdownEvents, [ticker, 5]),
    };
  }, [db, ticker]);

  if (loading) return <Layout title="Ticker Analysis"><p className="text-on-surface-variant">Loading database...</p></Layout>;
  if (error) return <Layout title="Ticker Analysis"><p className="text-danger">Failed to load database: {error.message}</p></Layout>;

  if (!ticker) {
    return (
      <Layout title="Ticker Analysis">
        <div className="bg-surface border border-outline-variant rounded-xl p-6 max-w-md">
          <h3 className="text-headline-sm text-on-surface mb-4">Select a ticker</h3>
          <select
            className="w-full h-9 px-3 border border-outline-variant rounded-lg bg-surface-container-lowest text-on-surface"
            value={pickerValue}
            onChange={(e) => {
              setPickerValue(e.target.value);
              if (e.target.value) navigate(`/analysis/${e.target.value}`);
            }}
          >
            <option value="">Choose a ticker...</option>
            {tickers.map((t) => (
              <option key={t.ticker} value={t.ticker}>
                {t.ticker} -- {t.name}
              </option>
            ))}
          </select>
        </div>
      </Layout>
    );
  }

  const { overview, distress, filings, insiders, drawdowns } = detail;
  if (!overview) {
    return (
      <Layout title="Ticker Analysis">
        <p className="text-danger">No data found for ticker "{ticker}".</p>
      </Layout>
    );
  }

  const latestFiling = filings?.[0];
  const rc = riskLevelChip(overview.distress_risk_level);
  const vc = verdictChip(overview.investment_verdict);
  const qLabel = scoreLabel(overview.quality_score);
  const iLabel = scoreLabel(overview.investment_score);
  const rLabel = riskScoreLabel(overview.risk_score);
  const isETF = overview.asset_type === "ETF";

  const range52wPct =
    overview.high_52w && overview.low_52w && overview.price
      ? ((overview.price - overview.low_52w) / (overview.high_52w - overview.low_52w)) * 100
      : null;

  const altmanZ = distress?.altman_z;
  const altmanZone = altmanZ == null ? null : altmanZ > 2.99 ? "Safe Zone (>2.99)" : altmanZ > 1.81 ? "Grey Zone (1.81-2.99)" : "Distress Zone (<1.81)";
  const altmanCls = altmanZ == null ? "text-on-surface-variant" : altmanZ > 2.99 ? "text-[#16a34a]" : altmanZ > 1.81 ? "text-[#eab308]" : "text-danger";
  const piotroski = distress?.piotroski_f;

  return (
    <Layout title={`${ticker} -- ${overview.name || ""}`}>
      <div className="flex items-center justify-between">
        <span className={`text-body-sm font-medium ${vc.cls} px-3 py-1 rounded-full`}>{vc.label}</span>
        <span className={`text-body-sm font-medium px-3 py-1 rounded-full ${rc.cls}`}>{rc.label}</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 flex flex-col justify-between">
          <span className="text-label-mono text-on-surface-variant uppercase flex items-center">Last Price<InfoTip text={STAT_INFO.lastPrice} /></span>
          <div className="flex items-end justify-between mt-2">
            <span className="text-display-lg text-primary">{fmtPrice(overview.price)}</span>
            <div className={`flex items-center text-data-tabular ${changeColorClass(overview.price_change_1d)}`}>
              {fmtPct(overview.price_change_1d)}
            </div>
          </div>
        </div>
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 flex flex-col justify-between">
          <span className="text-label-mono text-on-surface-variant uppercase flex items-center">52W Range<InfoTip text={STAT_INFO.range52w} /></span>
          <div className="mt-2">
            <div className="flex justify-between text-body-sm text-on-surface-variant mb-1">
              <span>{fmtPrice(overview.low_52w)}</span>
              <span>{fmtPrice(overview.high_52w)}</span>
            </div>
            <div className="w-full h-1.5 bg-surface-container rounded-full overflow-hidden relative">
              {range52wPct != null && (
                <div className="absolute top-0 bottom-0 w-1.5 bg-primary rounded-full" style={{ left: `${range52wPct}%` }} />
              )}
            </div>
          </div>
        </div>
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 flex flex-col justify-between">
          <span className="text-label-mono text-on-surface-variant uppercase flex items-center">P/E Ratio<InfoTip text={STAT_INFO.peRatio} /></span>
          <div className="flex items-end justify-between mt-2">
            <span className="text-headline-md text-primary">{overview.pe_ratio != null ? `${fmtNum(overview.pe_ratio, 1)}x` : "--"}</span>
          </div>
        </div>
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 flex flex-col justify-between">
          <span className="text-label-mono text-on-surface-variant uppercase flex items-center">Div Yield<InfoTip text={STAT_INFO.divYield} /></span>
          <div className="flex items-end justify-between mt-2">
            <span className="text-headline-md text-primary">{fmtPct(overview.dividend_yield_pct, 2)}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <ScoreGauge title="Quality Score" value={overview.quality_score} max={100} unit="Score Intensity" {...qLabel} barCls="bg-primary" tooltip={statText("qualityScore", isETF)} />
        <ScoreGauge title="Investment Score" value={overview.investment_score} max={100} unit="Conviction Level" {...iLabel} barCls="bg-secondary-container" tooltip={statText("investmentScore", isETF)} />
        <ScoreGauge title="Risk Score" value={overview.risk_score} max={100} unit="Risk Exposure" {...rLabel} barCls="bg-danger" tooltip={statText("riskScore", isETF)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
          <div className="p-4 border-b border-surface-container">
            <h3 className="text-headline-sm text-primary">Fundamental Summary</h3>
          </div>
          <div className="p-4 flex-1">
            {latestFiling ? (
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                    <th className="py-cell-padding-y px-cell-padding-x font-normal">Metric</th>
                    <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">{latestFiling.form_type} ({fmtDate(latestFiling.period_end_date)})</th>
                  </tr>
                </thead>
                <tbody className="text-data-tabular">
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x flex items-center">Revenue<InfoTip text={STAT_INFO.revenue} /></td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtCompact(latestFiling.revenue_usd)}</td>
                  </tr>
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x flex items-center">Net Income<InfoTip text={STAT_INFO.netIncome} /></td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtCompact(latestFiling.net_income_usd)}</td>
                  </tr>
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x flex items-center">FCF Yield<InfoTip text={STAT_INFO.fcfYield} /></td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPct(overview.fcf_yield_pct)}</td>
                  </tr>
                  <tr>
                    <td className="py-cell-padding-y px-cell-padding-x flex items-center">Debt-to-Equity<InfoTip text={STAT_INFO.debtToEquity} /></td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtNum(latestFiling.debt_to_equity_ratio, 2)}</td>
                  </tr>
                </tbody>
              </table>
            ) : (
              <p className="text-on-surface-variant text-body-sm">No SEC filings ingested for this ticker yet.</p>
            )}
          </div>
        </div>

        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
          <div className="p-4 border-b border-surface-container">
            <h3 className="text-headline-sm text-primary">Distress Models</h3>
          </div>
          <div className="p-4 flex-1 flex flex-col gap-4">
            <div className="bg-surface border border-surface-container rounded p-4">
              <div className="flex justify-between items-center mb-2">
                <span className="text-label-mono text-on-surface-variant uppercase flex items-center">Altman Z-Score<InfoTip text={statText("altmanZ", isETF)} /></span>
                <span className="text-headline-sm text-primary">{altmanZ != null ? fmtNum(altmanZ, 2) : "--"}</span>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex-1 h-2 bg-surface-container rounded-full overflow-hidden flex">
                  <div className="h-full bg-danger w-[20%]" />
                  <div className="h-full bg-[#eab308] w-[20%]" />
                  <div className="h-full bg-[#16a34a] w-[60%]" />
                </div>
                <span className={`text-label-mono ${altmanCls}`}>{altmanZone || "No data"}</span>
              </div>
            </div>
            <div className="bg-surface border border-surface-container rounded p-4">
              <div className="flex justify-between items-center mb-2">
                <span className="text-label-mono text-on-surface-variant uppercase flex items-center">Piotroski F-Score<InfoTip text={statText("piotroski", isETF)} /></span>
                <span className="text-headline-sm text-primary">{piotroski != null ? `${piotroski} / 9` : "--"}</span>
              </div>
              <div className="grid grid-cols-9 gap-1 h-2">
                {Array.from({ length: 9 }).map((_, i) => (
                  <div key={i} className={`rounded-sm h-full ${piotroski != null && i < piotroski ? "bg-[#16a34a]" : "bg-surface-container-high"}`} />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
        <div className="p-4 border-b border-surface-container">
          <h3 className="text-headline-sm text-primary">Options Chain</h3>
        </div>
        <OptionsChain ticker={ticker} price={overview.price} />
      </div>

      {insiders && insiders.length > 0 && (
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
          <div className="p-4 border-b border-surface-container">
            <h3 className="text-headline-sm text-primary">Recent Insider Trades</h3>
          </div>
          <div className="p-4 space-y-3">
            {insiders.map((t, i) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg border border-outline-variant/30">
                <div>
                  <div className="text-body-md font-semibold text-on-surface">{t.insider_name} {t.title ? `(${t.title})` : ""}</div>
                  <div className="text-body-sm text-on-surface-variant">{t.transaction_type || t.code}</div>
                </div>
                <div className="text-right">
                  <div className="font-data-tabular text-body-md font-medium">{t.total_value != null ? `$${(Number(t.total_value) / 1e6).toFixed(1)}M` : "--"}</div>
                  <div className="text-body-sm text-on-surface-variant">{fmtDate(t.filing_date)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Layout>
  );
}
