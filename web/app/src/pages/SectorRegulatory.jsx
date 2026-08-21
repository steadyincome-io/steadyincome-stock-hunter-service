import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fmtPct, fmtDate, changeColorClass } from "../lib/format";

function valuationTierChip(tier) {
  if (!tier) return { label: "N/A", cls: "bg-surface-variant text-on-surface-variant" };
  const t = tier.toLowerCase();
  if (t.includes("under")) return { label: tier, cls: "bg-[#10b981]/10 text-[#10b981]" };
  if (t.includes("over")) return { label: tier, cls: "bg-danger/10 text-danger" };
  return { label: tier, cls: "bg-surface-variant text-on-surface-variant" };
}

export default function SectorRegulatory() {
  const { db, loading, error, query } = useDatabase();
  const navigate = useNavigate();

  const data = useMemo(() => {
    if (!db) return null;
    return {
      sectors: query(QUERIES.sectorBreakdown) || [],
      insiders: query(QUERIES.recentInsiderTrades, [25]) || [],
      congress: query(QUERIES.recentCongressTrades, [25]) || [],
    };
  }, [db]);

  if (loading) return <Layout title="Sector & Regulatory Intelligence"><p className="text-on-surface-variant">Loading database...</p></Layout>;
  if (error) return <Layout title="Sector & Regulatory Intelligence"><p className="text-danger">Failed to load database: {error.message}</p></Layout>;

  const { sectors, insiders, congress } = data;
  const maxAbsSafety = Math.max(1, ...sectors.map((s) => Math.abs(s.avg_margin_of_safety_pct || 0)));

  return (
    <Layout title="Sector & Regulatory Intelligence">
      <div className="bg-surface-container-lowest rounded-xl border border-outline-variant overflow-hidden flex flex-col">
        <div className="px-6 py-4 border-b border-outline-variant/50 flex justify-between items-center">
          <h3 className="text-headline-sm text-on-surface">Sector Breakdown</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-surface-container-low text-on-surface-variant text-label-mono uppercase border-b border-outline-variant">
              <tr>
                <th className="px-cell-padding-x py-cell-padding-y font-medium">Sector</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium text-right">Ticker Count</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium">Avg Valuation</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium text-right">Avg Margin of Safety</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium text-right">Avg 1D Change</th>
              </tr>
            </thead>
            <tbody className="text-data-tabular text-on-surface divide-y divide-outline-variant/30">
              {sectors.map((s) => {
                const safety = s.avg_margin_of_safety_pct;
                const barWidth = `${(Math.abs(safety || 0) / maxAbsSafety) * 100}%`;
                const positive = (safety || 0) >= 0;
                return (
                  <tr key={s.sector} className="hover:bg-surface-container-low/50 transition-colors">
                    <td className="px-cell-padding-x py-cell-padding-y font-medium">{s.sector}</td>
                    <td className="px-cell-padding-x py-cell-padding-y text-right">{s.ticker_count}</td>
                    <td className="px-cell-padding-x py-cell-padding-y text-on-surface-variant text-body-sm">--</td>
                    <td className={`px-cell-padding-x py-cell-padding-y text-right ${positive ? "text-[#10b981]" : "text-danger"}`}>
                      {fmtPct(safety)}
                    </td>
                    <td className="px-cell-padding-x py-cell-padding-y text-right">
                      <div className="w-16 h-4 ml-auto bg-surface-container-low rounded relative overflow-hidden">
                        <div
                          className={`absolute left-0 top-0 bottom-0 opacity-80 ${positive ? "bg-[#10b981]" : "bg-danger"}`}
                          style={{ width: barWidth }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
              {sectors.length === 0 && (
                <tr>
                  <td className="px-cell-padding-x py-cell-padding-y text-on-surface-variant" colSpan={5}>
                    No scored sectors yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-surface-container-lowest rounded-xl border border-outline-variant overflow-hidden flex flex-col h-80">
          <div className="px-6 py-4 border-b border-outline-variant/50 flex justify-between items-center">
            <h3 className="text-headline-sm text-on-surface flex items-center gap-2">
              <span className="material-symbols-outlined text-[20px] text-primary-fixed-dim">person</span>
              Insider Trades (Form 4)
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {insiders.map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-3 rounded-lg border border-outline-variant/30 hover:bg-surface-container-low transition-colors cursor-pointer"
                onClick={() => navigate(`/analysis/${t.ticker}`)}
              >
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="font-data-tabular text-body-md font-semibold text-primary">{t.ticker}</span>
                    <span className="text-label-mono text-on-surface-variant">{t.transaction_type || t.code}</span>
                  </div>
                  <div className="text-body-sm text-on-surface-variant mt-1">
                    {t.insider_name} {t.title ? `(${t.title})` : ""}
                  </div>
                </div>
                <div className="text-right">
                  <div className={`font-data-tabular text-body-md font-medium ${changeColorClass(t.sentiment === "bullish" ? 1 : t.sentiment === "bearish" ? -1 : t.total_value)}`}>
                    {t.total_value != null ? `$${(Number(t.total_value) / 1e6).toFixed(1)}M` : "--"}
                  </div>
                  <div className="text-body-sm text-on-surface-variant">{fmtDate(t.filing_date)}</div>
                </div>
              </div>
            ))}
            {insiders.length === 0 && <p className="text-on-surface-variant text-body-sm">No insider trades recorded yet.</p>}
          </div>
        </div>

        <div className="bg-surface-container-lowest rounded-xl border border-outline-variant overflow-hidden flex flex-col h-80">
          <div className="px-6 py-4 border-b border-outline-variant/50 flex justify-between items-center">
            <h3 className="text-headline-sm text-on-surface flex items-center gap-2">
              <span className="material-symbols-outlined text-[20px] text-tertiary-fixed-dim">account_balance</span>
              Congressional Trades
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {congress.map((t, i) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg border border-outline-variant/30 hover:bg-surface-container-low transition-colors">
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="font-data-tabular text-body-md font-semibold text-primary">{t.ticker}</span>
                    <span className="text-label-mono text-on-surface-variant">{t.transaction_type}</span>
                  </div>
                  <div className="text-body-sm text-on-surface-variant mt-1">
                    {t.politician} ({t.party})
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-data-tabular text-body-md font-medium">{t.amount_range}</div>
                  <div className="text-body-sm text-on-surface-variant">{fmtDate(t.disclosure_date)}</div>
                </div>
              </div>
            ))}
            {congress.length === 0 && (
              <p className="text-on-surface-variant text-body-sm">
                No congressional trades tracked yet -- this table isn't populated by any worker currently.
              </p>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
}
