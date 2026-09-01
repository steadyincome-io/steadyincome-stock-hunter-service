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

// Column definitions for the sortable numeric columns -- accessor pulls the
// raw number (possibly nested under r.backtest) so sorting doesn't depend on
// how the cell happens to be formatted for display.
const SORTABLE_COLUMNS = {
  price: (r) => r.price,
  current_drawdown_pct: (r) => r.current_drawdown_pct,
  lowPrice: (r) => r.backtest?.lowPrice,
  daysSinceLow: (r) => r.backtest?.daysSinceLow,
  valueToday: (r) => r.backtest?.valueToday,
  gainDollars: (r) => r.backtest?.gainDollars,
  dividendsEarned: (r) => r.backtest?.dividendsEarned,
  annualizedReturnPct: (r) => r.backtest?.annualizedReturnPct,
  drawdowns_over_10pct: (r) => r.drawdowns_over_10pct,
  worst_drawdown_pct: (r) => r.worst_drawdown_pct,
};

function SortableTh({ label, sortKey, sort, onSort, className = "" }) {
  const active = sort.key === sortKey;
  return (
    <th
      className={`py-cell-padding-y px-cell-padding-x font-normal text-right cursor-pointer select-none hover:text-on-surface ${className}`}
      onClick={() => onSort(sortKey)}
    >
      {label}
      <span className="inline-block w-3 text-outline">{active ? (sort.dir === "asc" ? "↑" : "↓") : ""}</span>
    </th>
  );
}

export default function ETFBacktestScreener() {
  const { db, loading, error, query, queryOne } = useDatabase();
  const navigate = useNavigate();
  const [sort, setSort] = useState({ key: null, dir: "desc" });

  const rows = useMemo(() => {
    if (!db) return [];
    const etfs = query(QUERIES.etfBacktestList) || [];
    const today = new Date().toISOString().slice(0, 10);
    return etfs.map((etf) => {
      const low = queryOne(QUERIES.etf52wLowDate, [etf.ticker]);
      let backtest = null;
      if (low && low.close_price > 0 && etf.price != null) {
        const shares = INVESTED_AMOUNT / low.close_price;
        const valueToday = shares * etf.price;
        const div = queryOne(QUERIES.dividendsBetween, [etf.ticker, low.trade_date, today]);
        const dividendsEarned = shares * (div?.total_per_share || 0);
        const daysSinceLow = daysSince(low.trade_date, etf.updated_at);
        const totalReturnMultiple = (valueToday + dividendsEarned) / INVESTED_AMOUNT;
        // CAGR-style compounding, not a linear *365/days scale-up -- linear
        // annualization badly overstates short holding periods (e.g. a 5%
        // gain in 10 days would linearly "annualize" to 182%). This is a
        // realized-return annualization, not a forecast that the same rate
        // continues -- undefined (returns null) for a same-day low.
        const annualizedReturnPct = daysSinceLow > 0
          ? (Math.pow(totalReturnMultiple, 365 / daysSinceLow) - 1) * 100
          : null;
        backtest = {
          lowDate: low.trade_date,
          lowPrice: low.close_price,
          valueToday,
          gainDollars: valueToday - INVESTED_AMOUNT,
          gainPct: ((etf.price - low.close_price) / low.close_price) * 100,
          daysSinceLow,
          dividendsEarned,
          annualizedReturnPct,
        };
      }
      return { ...etf, backtest };
    });
  }, [db]);

  const sortedRows = useMemo(() => {
    if (!sort.key) return rows;
    const accessor = SORTABLE_COLUMNS[sort.key];
    const withVal = rows.map((r) => ({ r, v: accessor(r) }));
    withVal.sort((a, b) => {
      // Rows missing this value (e.g. no backtest) always sort last, regardless of direction.
      if (a.v == null && b.v == null) return 0;
      if (a.v == null) return 1;
      if (b.v == null) return -1;
      return sort.dir === "asc" ? a.v - b.v : b.v - a.v;
    });
    return withVal.map((x) => x.r);
  }, [rows, sort]);

  function handleSort(key) {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }

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
            "Recent 52-week low" is the lowest daily close in the trailing 252 trading days. Dividends Earned sums
            actual per-share distributions (`dividend_history`, sourced from yfinance) paid between that low date
            and today. Drawdown counts are completed episodes (peak-to-recovery) of at least 10% from
            `drawdown_summary`, computed by the pipeline from full price history -- not limited to the last year.
            Annualized Return compounds (price gain + dividends) over the actual holding period into a CAGR-style
            annual rate -- ((endValue/startValue)^(365/daysHeld) - 1) -- not a linear x365/days scale-up, since that
            would badly overstate short holds. It's an annualization of the realized return, not a forecast that
            the same rate continues. Click a numeric column header to sort.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                <th className="py-cell-padding-y px-cell-padding-x font-normal">Ticker</th>
                <SortableTh label="Price" sortKey="price" sort={sort} onSort={handleSort} />
                <SortableTh label="Current DD" sortKey="current_drawdown_pct" sort={sort} onSort={handleSort} />
                <SortableTh label="52W Low" sortKey="lowPrice" sort={sort} onSort={handleSort} />
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Low Date</th>
                <SortableTh label="Days Since" sortKey="daysSinceLow" sort={sort} onSort={handleSort} />
                <SortableTh label="$10K Then -> Now" sortKey="valueToday" sort={sort} onSort={handleSort} />
                <SortableTh label="Gain" sortKey="gainDollars" sort={sort} onSort={handleSort} />
                <SortableTh label="Dividends Earned" sortKey="dividendsEarned" sort={sort} onSort={handleSort} />
                <SortableTh label="Annualized Return" sortKey="annualizedReturnPct" sort={sort} onSort={handleSort} />
                <SortableTh label="# Drawdowns >=10%" sortKey="drawdowns_over_10pct" sort={sort} onSort={handleSort} />
                <SortableTh label="Worst DD" sortKey="worst_drawdown_pct" sort={sort} onSort={handleSort} />
              </tr>
            </thead>
            <tbody className="text-data-tabular">
              {sortedRows.map((r) => (
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
                      <td className="py-cell-padding-y px-cell-padding-x text-right text-success">
                        {r.backtest.dividendsEarned > 0 ? `+${fmtPrice(r.backtest.dividendsEarned)}` : fmtPrice(0)}
                      </td>
                      <td className={`py-cell-padding-y px-cell-padding-x text-right font-medium ${changeColorClass(r.backtest.annualizedReturnPct)}`}>
                        {r.backtest.annualizedReturnPct != null ? fmtPct(r.backtest.annualizedReturnPct) : "--"}
                      </td>
                    </>
                  ) : (
                    <td colSpan={7} className="py-cell-padding-y px-cell-padding-x text-on-surface-variant text-center">
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
