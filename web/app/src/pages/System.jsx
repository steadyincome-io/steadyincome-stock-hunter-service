import { useMemo } from "react";
import Layout from "../components/Layout";
import { useDatabase } from "../lib/useDatabase";
import { QUERIES } from "../lib/queries";
import { fmtDate } from "../lib/format";

function statusChip(status) {
  const s = (status || "").toLowerCase();
  if (s === "success") return "bg-success/10 text-success";
  if (s === "failed" || s === "error") return "bg-danger/10 text-danger";
  return "bg-warning/10 text-warning";
}

export default function System() {
  const { db, loading, error, query } = useDatabase();

  const runs = useMemo(() => (db ? query(QUERIES.pipelineRuns, [50]) : []), [db]);

  if (loading) return <Layout title="System"><p className="text-on-surface-variant">Loading database...</p></Layout>;
  if (error) return <Layout title="System"><p className="text-danger">Failed to load database: {error.message}</p></Layout>;

  return (
    <Layout title="System">
      <div className="bg-surface border border-outline-variant rounded-xl overflow-hidden flex flex-col">
        <div className="p-4 border-b border-outline-variant bg-surface-bright">
          <h3 className="text-headline-sm text-on-surface m-0">Pipeline Run History</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-surface-container-low text-on-surface-variant text-label-mono uppercase border-b border-outline-variant">
              <tr>
                <th className="px-cell-padding-x py-cell-padding-y font-medium">Run ID</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium">Timestamp</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium text-right">Duration</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium text-right">Tickers Processed</th>
                <th className="px-cell-padding-x py-cell-padding-y font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="text-data-tabular text-on-surface divide-y divide-outline-variant/30">
              {runs.map((r) => (
                <tr key={r.run_id} className="hover:bg-surface-container-low transition-colors">
                  <td className="px-cell-padding-x py-cell-padding-y font-mono text-body-sm">{r.run_id}</td>
                  <td className="px-cell-padding-x py-cell-padding-y">{fmtDate(r.run_timestamp)}</td>
                  <td className="px-cell-padding-x py-cell-padding-y text-right">{r.duration_seconds != null ? `${r.duration_seconds}s` : "--"}</td>
                  <td className="px-cell-padding-x py-cell-padding-y text-right">{r.tickers_processed ?? "--"}</td>
                  <td className="px-cell-padding-x py-cell-padding-y">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${statusChip(r.status)}`}>
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr>
                  <td className="px-cell-padding-x py-cell-padding-y text-on-surface-variant" colSpan={5}>
                    No pipeline runs recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </Layout>
  );
}
