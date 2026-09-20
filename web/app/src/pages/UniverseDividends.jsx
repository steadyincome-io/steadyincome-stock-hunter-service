import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fmtDate, fmtPct, fmtPrice } from "../lib/format";
import { fetchLivePrices } from "../lib/optionsApi";

const SORT_COLUMNS = {
  ticker: (r) => r.ticker,
  price: (r) => r.price,
  low_52w: (r) => r.low_52w,
  high_52w: (r) => r.high_52w,
  current_drawdown_pct: (r) => r.current_drawdown_pct,
  investment_score: (r) => r.investment_score,
  yearly_dividend: (r) => r.yearly_dividend,
  dividend_since: (r) => r.dividend_since,
  dividend_ever_decreased: (r) => r.dividend_ever_decreased,
};

function SortableHeader({ label, sortKey, sort, onSort, align = "right" }) {
  const active = sort.key === sortKey;
  return (
    <th
      className={`py-cell-padding-y px-cell-padding-x font-normal cursor-pointer select-none hover:text-on-surface text-${align}`}
      onClick={() => onSort(sortKey)}
    >
      {label}
      <span className="inline-block w-3 text-outline">{active ? (sort.dir === "asc" ? "↑" : "↓") : ""}</span>
    </th>
  );
}

function DividendStatus({ value, hasDividend }) {
  if (!hasDividend) return <span className="text-outline">--</span>;
  return value === 1
    ? <span className="text-danger">Yes</span>
    : <span className="text-success">No</span>;
}

export default function UniverseDividends() {
  const { db, loading, error, query } = useDatabase();
  const navigate = useNavigate();
  const [sort, setSort] = useState({ key: "ticker", dir: "asc" });
  const [search, setSearch] = useState("");
  const [livePrices, setLivePrices] = useState({});

  const rows = useMemo(() => (db ? query(QUERIES.universeDividendTable) || [] : []), [db, query]);
  useEffect(() => {
    if (!rows.length) {
      setLivePrices({});
      return undefined;
    }
    const controller = new AbortController();
    fetchLivePrices(rows.map((row) => row.ticker), { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setLivePrices(data.prices || {});
      })
      .catch(() => {
        if (!controller.signal.aborted) setLivePrices({});
      });
    return () => controller.abort();
  }, [rows]);

  const pricedRows = useMemo(() => rows.map((row) => {
    const livePrice = livePrices[row.ticker]?.price;
    const price = livePrice != null ? livePrice : row.price;
    const currentDrawdown = price != null && row.high_52w > 0
      ? ((price - row.high_52w) / row.high_52w) * 100
      : row.current_drawdown_pct;
    return {
      ...row,
      price,
      current_drawdown_pct: currentDrawdown,
      price_source: livePrice != null ? "live" : "snapshot",
    };
  }), [rows, livePrices]);

  const filteredRows = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = term
      ? pricedRows.filter((row) => `${row.ticker} ${row.name || ""}`.toLowerCase().includes(term))
      : pricedRows;
    const accessor = SORT_COLUMNS[sort.key];
    return [...filtered].sort((a, b) => {
      const av = accessor(a);
      const bv = accessor(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const comparison = typeof av === "string" ? av.localeCompare(String(bv)) : Number(av) - Number(bv);
      return sort.dir === "asc" ? comparison : -comparison;
    });
  }, [pricedRows, search, sort]);

  function handleSort(key) {
    setSort((current) => current.key === key
      ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
      : { key, dir: key === "ticker" ? "asc" : "desc" });
  }

  if (loading) return <Layout title="Universe Dividends"><p className="text-on-surface-variant">Loading database...</p></Layout>;
  if (error) return <Layout title="Universe Dividends"><p className="text-danger">Failed to load database: {error.message}</p></Layout>;

  return (
    <Layout title="Universe Dividends">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-headline-sm text-primary">Active Universe Dividend Monitor</h2>
            <p className="text-body-sm text-on-surface-variant mt-1">
              Current snapshot prices and 52-week range for every active ticker. Yearly dividend is the trailing
              365-day per-share total; decrease history compares completed calendar years.
            </p>
          </div>
          <label className="text-label-mono text-on-surface-variant uppercase tracking-wide">
            Search
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Ticker or name"
              className="mt-1 block w-56 rounded border border-outline-variant bg-surface-container-lowest px-3 py-2 text-body-sm text-on-surface outline-none focus:border-primary"
            />
          </label>
        </div>

        <div className="bg-surface-container-lowest border border-outline-variant rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-surface-container text-body-sm text-on-surface-variant">
            <span>{filteredRows.length} active ticker{filteredRows.length === 1 ? "" : "s"}</span>
            <span>Prices: live backend when available; snapshot fallback</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[1080px]">
              <thead>
                <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                  <SortableHeader label="Ticker" sortKey="ticker" sort={sort} onSort={handleSort} align="left" />
                  <th className="py-cell-padding-y px-cell-padding-x font-normal text-left">Name</th>
                  <SortableHeader label="Current Price" sortKey="price" sort={sort} onSort={handleSort} />
                  <SortableHeader label="52W Low" sortKey="low_52w" sort={sort} onSort={handleSort} />
                  <SortableHeader label="52W High" sortKey="high_52w" sort={sort} onSort={handleSort} />
                  <SortableHeader label="Current DD" sortKey="current_drawdown_pct" sort={sort} onSort={handleSort} />
                  <SortableHeader label="Investment Score" sortKey="investment_score" sort={sort} onSort={handleSort} />
                  <SortableHeader label="Yearly Dividend" sortKey="yearly_dividend" sort={sort} onSort={handleSort} />
                  <SortableHeader label="Dividend Since" sortKey="dividend_since" sort={sort} onSort={handleSort} />
                  <SortableHeader label="Ever Decreased" sortKey="dividend_ever_decreased" sort={sort} onSort={handleSort} />
                </tr>
              </thead>
              <tbody className="text-data-tabular">
                {filteredRows.map((row) => {
                  const hasDividend = row.dividend_since != null;
                  return (
                    <tr key={row.ticker} className="border-b border-surface-container hover:bg-surface-container-low">
                      <td className="py-cell-padding-y px-cell-padding-x text-left">
                        <button className="text-primary font-semibold hover:underline" onClick={() => navigate(`/analysis/${encodeURIComponent(row.ticker)}`)}>
                          {row.ticker}
                        </button>
                        <div className="text-[10px] text-outline uppercase">{row.asset_type}</div>
                      </td>
                      <td className="py-cell-padding-y px-cell-padding-x text-left text-on-surface-variant max-w-[260px] truncate" title={row.name || ""}>{row.name || "--"}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">
                        <div>{fmtPrice(row.price)}</div>
                        <div className={`text-[10px] uppercase tracking-wide ${row.price_source === "live" ? "text-success" : "text-outline"}`}>
                          {row.price_source}
                        </div>
                      </td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(row.low_52w)}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(row.high_52w)}</td>
                      <td className={`py-cell-padding-y px-cell-padding-x text-right ${Number(row.current_drawdown_pct) < 0 ? "text-danger" : "text-success"}`}>{fmtPct(row.current_drawdown_pct, 1)}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right font-semibold text-primary">{row.investment_score ?? "--"}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">{hasDividend ? fmtPrice(row.yearly_dividend) : "--"}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right">{hasDividend ? fmtDate(row.dividend_since) : "--"}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right"><DividendStatus value={row.dividend_ever_decreased} hasDividend={hasDividend} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Layout>
  );
}
