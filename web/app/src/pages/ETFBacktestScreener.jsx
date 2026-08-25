import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fmtPrice, fmtPct, fmtDate, changeColorClass } from "../lib/format";

const INVESTED_AMOUNT = 10000;

// Days since the 52-week-low date, using the snapshot's updated_at as "today"
// (the pipeline's own notion of "now") rather than the browser's clock, so
// this stays consistent with the rest of the data even if it's stale.
function daysSince(fromDateStr, toDateStr) {
  if (!fromDateStr || !toDateStr) return null;
  const from = new Date(fromDateStr);
  const to = new Date(toDateStr);
  return Math.max(0, Math.round((to - from) / 86400000));
}

export default function ETFBacktestScreener() {
  const { db, loading, error, query, queryOne } = useDatabase();
  const navigate = useNavigate();

  const rows = useMemo(() => {
    if (!db) return [];
    const etfs = query(QUERIES.etfBacktestList) || [];
    return etfs.map((etf) => {
      const low = queryOne(QUERIES.etf52wLowDate, [etf.ticker]);
      let backtest = null;
      if (low && low.close_price > 0 && etf.price != null) {
        const shares = INVESTED_AMOUNT / low.close_price;
        const valueToday = shares * etf.price;
        backtest = {
          lowDate: low.trade_date,
          lowPrice: low.close_price,
          valueToday,
          gainDollars: valueToday - INVESTED_AMOUNT,
          gainPct: ((etf.price - low.close_price) / low.close_price) * 100,
          daysSinceLow: daysSince(low.trade_date, etf.updated_at),
        };
      }
      return { ...etf, backtest };
    });
  }, [db]);

  if (loading) return <Layout title="ETF Backtest Screener"><p className="text-on-surface-variant">Loading database...</p></Layout>;
  if (error) return <Layout title="ETF Backtest Screener"><p className="text-danger">Failed to load database: {error.message}</p></Layout>;

  return (
    <Layout title="ETF Backtest Screener">
      <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
        <div className="p-4 border-b border-surface-container">
          <h3 className="text-headline-sm text-primary">
            If you'd invested {fmtPrice(INVESTED_AMOUNT)} at each ETF's most recent 52-week low
          </h3>
          <p className="text-body-sm text-on-surface-variant mt-1">
            "Recent 52-week low" is the lowest daily close in the trailing 252 trading days. Drawdown counts are
            completed episodes (peak-to-recovery) of at least 10% from `drawdown_summary`, computed by the pipeline
            from full price history -- not limited to the last year.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                <th className="py-cell-padding-y px-cell-padding-x font-normal">Ticker</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Price</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Current DD</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">52W Low</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Low Date</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Days Since</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">$10K Then -&gt; Now</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Gain</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right"># Drawdowns &ge;10%</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Worst DD</th>
              </tr>
            </thead>
            <tbody className="text-data-tabular">
              {rows.map((r) => (
                <tr
                  key={r.ticker}
                  className="border-b border-surface-container cursor-pointer hover:bg-surface-container-high/50"
                  onClick={() => navigate(`/analysis/${r.ticker}`)}
                >
                  <td className="py-cell-padding-y px-cell-padding-x font-bold text-on-surface">{r.ticker}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(r.price)}</td>
                  <td className={`py-cell-padding-y px-cell-padding-x text-right ${changeColorClass(r.current_drawdown_pct)}`}>
                    {r.current_drawdown_pct != null ? fmtPct(r.current_drawdown_pct) : "--"}
                  </td>
                  {r.backtest ? (
                    <>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(r.backtest.lowPrice)}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface-variant">{fmtDate(r.backtest.lowDate)}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface-variant">
                        {r.backtest.daysSinceLow != null ? `${r.backtest.daysSinceLow}d` : "--"}
                      </td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">
                        {fmtPrice(INVESTED_AMOUNT)} <span className="text-on-surface-variant">&rarr;</span> {fmtPrice(r.backtest.valueToday)}
                      </td>
                      <td className={`py-cell-padding-y px-cell-padding-x text-right font-medium ${changeColorClass(r.backtest.gainDollars)}`}>
                        {r.backtest.gainDollars >= 0 ? "+" : ""}{fmtPrice(r.backtest.gainDollars)} ({fmtPct(r.backtest.gainPct)})
                      </td>
                    </>
                  ) : (
                    <td colSpan={5} className="py-cell-padding-y px-cell-padding-x text-on-surface-variant text-center">
                      Not enough price history
                    </td>
                  )}
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{r.drawdowns_over_10pct ?? "--"}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right text-danger">
                    {r.worst_drawdown_pct != null ? fmtPct(r.worst_drawdown_pct) : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <footer className="pt-4 border-t border-outline-variant flex justify-between items-center text-label-mono text-outline">
        <div>{rows.length} active ETFs</div>
      </footer>
    </Layout>
  );
}
