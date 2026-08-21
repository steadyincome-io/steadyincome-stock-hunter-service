import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fmtMarketCapB, fmtNum, fmtPct, changeColorClass, riskLevelChip, verdictChip } from "../lib/format";

const RISK_COLORS = { low: "#10b981", high: "#ef4444", distress: "#7f1d1d", unscored: "#c6c6cd" };
const RISK_LABELS = { low: "Low Risk", high: "Elevated Risk", distress: "Distress Zone", unscored: "Unscored" };

export default function MarketOverview() {
  const { db, loading, error, queryOne, query } = useDatabase();
  const navigate = useNavigate();

  const stats = useMemo(() => {
    if (!db) return null;
    const counts = queryOne(QUERIES.universeCounts) || {};
    const cap = queryOne(QUERIES.totalMarketCapB) || {};
    const quality = queryOne(QUERIES.avgQualityScore) || {};
    const drawdown = queryOne(QUERIES.avgMarketDrawdown) || {};
    const risk = query(QUERIES.riskDistribution) || [];
    const universe = query(QUERIES.universeTable) || [];
    return { counts, cap, quality, drawdown, risk, universe };
  }, [db]);

  if (loading) return <Layout title="Market Overview"><p className="text-on-surface-variant">Loading database...</p></Layout>;
  if (error) return <Layout title="Market Overview"><p className="text-danger">Failed to load database: {error.message}</p></Layout>;

  const { counts, cap, quality, drawdown, risk, universe } = stats;
  const riskTotal = risk.reduce((s, r) => s + r.n, 0) || 1;

  return (
    <Layout title="Market Overview">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total Tickers"
          icon="list_alt"
          value={counts.total ?? 0}
          sub={
            <>
              <span className="text-secondary font-medium">{counts.active ?? 0} Active</span>
              <span className="text-outline">/ {counts.inactive ?? 0} Inactive</span>
            </>
          }
        />
        <StatCard
          label="Total Market Cap"
          icon="account_balance"
          value={fmtMarketCapB(cap.total_b)}
          sub={<span className="text-outline">Scan: $50B+</span>}
        />
        <StatCard
          label="Avg Quality Score"
          icon="speed"
          value={quality.avg_score != null ? fmtNum(quality.avg_score, 0) : "--"}
          suffix="/100"
          sub={<span className="text-outline">{quality.n ?? 0} scored</span>}
        />
        <StatCard
          label="Market Drawdown"
          icon="trending_down"
          value={drawdown.avg_dd != null ? fmtPct(drawdown.avg_dd, 1) : "--"}
          sub={<span className="text-outline">{drawdown.n ?? 0} scored</span>}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-9 bg-surface border border-outline-variant rounded-xl overflow-hidden flex flex-col h-[600px]">
          <div className="p-4 border-b border-outline-variant flex justify-between items-center bg-surface-bright shrink-0">
            <h3 className="text-headline-sm text-on-surface m-0">Universe &amp; Daily Snapshot</h3>
          </div>
          <div className="flex-1 overflow-auto">
            <table className="w-full text-left border-collapse min-w-[800px]">
              <thead className="sticky top-0 bg-surface-bright shadow-[0_1px_0_#c6c6cd] z-10">
                <tr>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap">Ticker</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap">Name</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap">Sector</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap text-right">Price</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap text-right">1D Change</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap text-center">Quality</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap text-center">Risk Level</th>
                  <th className="py-cell-padding-y px-cell-padding-x text-label-mono text-on-surface-variant font-medium whitespace-nowrap">Verdict</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant/50 text-data-tabular bg-surface">
                {universe.map((row) => {
                  const rc = riskLevelChip(row.distress_risk_level);
                  const vc = verdictChip(row.investment_verdict);
                  return (
                    <tr
                      key={row.ticker}
                      className="hover:bg-surface-container-low transition-colors cursor-pointer"
                      onClick={() => navigate(`/analysis/${row.ticker}`)}
                    >
                      <td className="py-cell-padding-y px-cell-padding-x font-bold text-on-surface">{row.ticker}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-body-md text-on-surface truncate max-w-[150px]">{row.name}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-body-sm text-on-surface-variant">{row.sector || "--"}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface tabular-nums">{row.price != null ? `$${Number(row.price).toFixed(2)}` : "--"}</td>
                      <td className={`py-cell-padding-y px-cell-padding-x text-right tabular-nums ${changeColorClass(row.price_change_1d)}`}>{fmtPct(row.price_change_1d)}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-center text-on-surface tabular-nums">{row.quality_score != null ? fmtNum(row.quality_score, 1) : "--"}</td>
                      <td className="py-cell-padding-y px-cell-padding-x text-center">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${rc.cls}`}>{rc.label}</span>
                      </td>
                      <td className={`py-cell-padding-y px-cell-padding-x text-body-sm font-medium ${vc.cls}`}>{vc.label}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="xl:col-span-3 space-y-6 flex flex-col h-[600px]">
          <div className="bg-surface border border-outline-variant rounded-xl p-5 flex-1 flex flex-col">
            <h3 className="text-headline-sm text-on-surface mb-4 pb-2 border-b border-outline-variant shrink-0">Risk Distribution</h3>
            <div className="flex-1 flex flex-col justify-center">
              <div className="text-center mb-6">
                <span className="text-display-lg text-on-surface leading-none block">{counts.active ?? 0}</span>
                <span className="text-label-mono text-outline mt-1">TOTAL SCORED</span>
              </div>
              <div className="space-y-3">
                {["low", "high", "distress", "unscored"].map((bucket) => {
                  const n = risk.find((r) => r.bucket === bucket)?.n ?? 0;
                  const pct = ((n / riskTotal) * 100).toFixed(0);
                  return (
                    <div key={bucket} className="flex justify-between items-center text-body-sm">
                      <div className="flex items-center gap-2">
                        <div className="w-3 h-3 rounded-sm" style={{ background: RISK_COLORS[bucket] }} />
                        <span className="text-on-surface-variant">{RISK_LABELS[bucket]}</span>
                      </div>
                      <span className="font-data-tabular font-medium text-on-surface">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>

      <footer className="pt-4 border-t border-outline-variant flex justify-between items-center text-label-mono text-outline">
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-success" />
          <span>System Operational</span>
        </div>
        <div>{universe.length} active tickers loaded</div>
      </footer>
    </Layout>
  );
}

function StatCard({ label, icon, value, suffix, sub }) {
  return (
    <div className="bg-surface border border-outline-variant rounded-xl p-4 hover:shadow-[0_4px_16px_rgba(0,0,0,0.04)] transition-shadow">
      <div className="flex justify-between items-start mb-2">
        <span className="text-label-mono text-on-surface-variant uppercase">{label}</span>
        <span className="material-symbols-outlined text-outline text-sm">{icon}</span>
      </div>
      <div className="text-display-lg text-on-surface">
        {value}
        {suffix && <span className="text-headline-sm text-outline">{suffix}</span>}
      </div>
      {sub && <div className="mt-2 flex items-center gap-2 text-body-sm">{sub}</div>}
    </div>
  );
}
