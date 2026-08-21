import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fmtPrice, fmtPct, fmtNum, fmtDate, fmtCompact, changeColorClass, riskLevelChip, verdictChip } from "../lib/format";

function ScoreGauge({ title, value, max, label, labelCls, barCls, unit }) {
  const pct = value != null ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-6 flex flex-col items-center">
      <h3 className="text-headline-sm text-primary mb-6 w-full text-left border-b border-surface-container pb-2">{title}</h3>
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
          <span className="text-label-mono text-on-surface-variant uppercase">Last Price</span>
          <div className="flex items-end justify-between mt-2">
            <span className="text-display-lg text-primary">{fmtPrice(overview.price)}</span>
            <div className={`flex items-center text-data-tabular ${changeColorClass(overview.price_change_1d)}`}>
              {fmtPct(overview.price_change_1d)}
            </div>
          </div>
        </div>
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 flex flex-col justify-between">
          <span className="text-label-mono text-on-surface-variant uppercase">52W Range</span>
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
          <span className="text-label-mono text-on-surface-variant uppercase">P/E Ratio</span>
          <div className="flex items-end justify-between mt-2">
            <span className="text-headline-md text-primary">{overview.pe_ratio != null ? `${fmtNum(overview.pe_ratio, 1)}x` : "--"}</span>
          </div>
        </div>
        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 flex flex-col justify-between">
          <span className="text-label-mono text-on-surface-variant uppercase">Div Yield</span>
          <div className="flex items-end justify-between mt-2">
            <span className="text-headline-md text-primary">{fmtPct(overview.dividend_yield_pct, 2)}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <ScoreGauge title="Quality Score" value={overview.quality_score} max={100} unit="Score Intensity" {...qLabel} barCls="bg-primary" />
        <ScoreGauge title="Investment Score" value={overview.investment_score} max={100} unit="Conviction Level" {...iLabel} barCls="bg-secondary-container" />
        <ScoreGauge title="Risk Score" value={overview.risk_score} max={100} unit="Risk Exposure" {...rLabel} barCls="bg-danger" />
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
                    <td className="py-cell-padding-y px-cell-padding-x">Revenue</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtCompact(latestFiling.revenue_usd)}</td>
                  </tr>
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x">Net Income</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtCompact(latestFiling.net_income_usd)}</td>
                  </tr>
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x">FCF Yield</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPct(overview.fcf_yield_pct)}</td>
                  </tr>
                  <tr>
                    <td className="py-cell-padding-y px-cell-padding-x">Debt-to-Equity</td>
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
                <span className="text-label-mono text-on-surface-variant uppercase">Altman Z-Score</span>
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
                <span className="text-label-mono text-on-surface-variant uppercase">Piotroski F-Score</span>
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
        <div className="p-6 flex flex-col items-center text-center gap-2">
          <span className="material-symbols-outlined text-outline text-3xl">info</span>
          <p className="text-on-surface-variant text-body-sm max-w-md">
            Live option chains aren't persisted to this database -- they're fetched fresh at screening time by
            premium_screener.py. Run the screener locally for current strikes, deltas, and premiums on {ticker}.
          </p>
        </div>
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
