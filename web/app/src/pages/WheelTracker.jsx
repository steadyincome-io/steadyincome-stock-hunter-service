import { Fragment, useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import { fetchTrades, createTrade, updateTrade, deleteTrade, fetchLiveQuote } from "../lib/tradesApi";
import { fmtPrice, fmtDate, fmtNum } from "../lib/format";

const STRATEGY_LABEL = { cash_secured_put: "Cash Secured Put", covered_call: "Covered Call" };
const STATUS_LABEL = { open: "open", expired: "expired", assigned: "assigned", closed_early: "BTC" };
const STATUS_CLS = {
  open: "bg-success/10 text-success",
  expired: "bg-warning/10 text-warning",
  assigned: "bg-info/10 text-info",
  closed_early: "bg-outline-variant/30 text-on-surface-variant",
};

function signedPrice(v) {
  if (v == null) return "--";
  const n = Number(v);
  return `${n >= 0 ? "+" : "-"}${fmtPrice(Math.abs(n))}`;
}

// A single-line pill instead of a bare colored number -- avoids the "+" and
// the amount visually splitting onto separate lines in a narrow column, and
// reads more clearly at a glance than plain colored text.
function PLBadge({ value }) {
  if (value == null) {
    return <span className="text-on-surface-variant">--</span>;
  }
  const positive = value >= 0;
  return (
    <span
      className={`inline-flex items-center gap-0.5 whitespace-nowrap px-2 py-0.5 rounded-full text-[12px] font-semibold ${
        positive ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
      }`}
    >
      <span className="material-symbols-outlined !text-[13px] !leading-none">{positive ? "arrow_upward" : "arrow_downward"}</span>
      {signedPrice(value)}
    </span>
  );
}

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4">
      <span className="text-label-mono text-on-surface-variant uppercase">{label}</span>
      <div className="text-display-lg text-on-surface mt-1">{value}</div>
      {sub && <div className="text-body-sm text-on-surface-variant mt-1">{sub}</div>}
    </div>
  );
}

function NewTradeForm({ onCreate, onCancel }) {
  const [form, setForm] = useState({
    ticker: "",
    strategy: "cash_secured_put",
    strike: "",
    qty: 1,
    premium_collected: "",
    delta_at_open: "",
    open_date: new Date().toISOString().slice(0, 10),
    expiration_date: "",
    open_fees: "0",
    notes: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState(null);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    setErr(null);
    try {
      await onCreate({
        ...form,
        ticker: form.ticker.toUpperCase().trim(),
        strike: parseFloat(form.strike),
        qty: parseInt(form.qty, 10),
        premium_collected: parseFloat(form.premium_collected),
        delta_at_open: form.delta_at_open === "" ? null : parseFloat(form.delta_at_open),
        open_fees: form.open_fees === "" ? 0 : parseFloat(form.open_fees),
      });
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setSubmitting(false);
    }
  }

  const inputCls = "h-9 px-3 border border-outline-variant rounded-lg bg-surface-container-lowest text-on-surface text-body-sm w-full";

  return (
    <form onSubmit={submit} className="bg-surface-container-lowest border border-outline-variant rounded-lg p-4 grid grid-cols-2 md:grid-cols-4 gap-3">
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Ticker</label>
        <input required className={inputCls} value={form.ticker} onChange={set("ticker")} placeholder="NFLX" />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Strategy</label>
        <select className={inputCls} value={form.strategy} onChange={set("strategy")}>
          <option value="cash_secured_put">Cash Secured Put</option>
          <option value="covered_call">Covered Call</option>
        </select>
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Strike</label>
        <input required type="number" step="0.01" className={inputCls} value={form.strike} onChange={set("strike")} />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Qty (contracts)</label>
        <input required type="number" min="1" className={inputCls} value={form.qty} onChange={set("qty")} />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Premium Collected ($)</label>
        <input required type="number" step="0.01" className={inputCls} value={form.premium_collected} onChange={set("premium_collected")} />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Commission / Fees to Open ($)</label>
        <input type="number" step="0.01" className={inputCls} value={form.open_fees} onChange={set("open_fees")} />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Delta @ Open (optional)</label>
        <input type="number" step="0.01" className={inputCls} value={form.delta_at_open} onChange={set("delta_at_open")} />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Open Date</label>
        <input required type="date" className={inputCls} value={form.open_date} onChange={set("open_date")} />
      </div>
      <div>
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Expiration</label>
        <input required type="date" className={inputCls} value={form.expiration_date} onChange={set("expiration_date")} />
      </div>
      <div className="col-span-2 md:col-span-4">
        <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Notes (optional)</label>
        <input className={inputCls} value={form.notes} onChange={set("notes")} />
      </div>
      {err && <div className="col-span-2 md:col-span-4 text-danger text-body-sm">{err}</div>}
      <div className="col-span-2 md:col-span-4 flex gap-2 justify-end">
        <button type="button" onClick={onCancel} className="h-9 px-4 rounded-lg border border-outline-variant text-body-sm text-on-surface-variant">
          Cancel
        </button>
        <button type="submit" disabled={submitting} className="h-9 px-4 rounded-lg bg-primary text-on-primary text-body-sm font-medium disabled:opacity-50">
          {submitting ? "Saving..." : "Add Trade"}
        </button>
      </div>
    </form>
  );
}

function CloseTradeForm({ trade, onClose, onCancel }) {
  const [status, setStatus] = useState("expired");
  const [closeDate, setCloseDate] = useState(new Date().toISOString().slice(0, 10));
  const [closeCost, setCloseCost] = useState("0");
  const [fees, setFees] = useState("0");
  const [submitting, setSubmitting] = useState(false);
  const inputCls = "h-9 px-3 border border-outline-variant rounded-lg bg-surface-container-lowest text-on-surface text-body-sm w-full";

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onClose(trade.id, {
        status,
        close_date: closeDate,
        close_cost: parseFloat(closeCost) || 0,
        fees: parseFloat(fees) || 0,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <tr className="bg-surface-container-high/40">
      <td colSpan={14} className="p-4">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <span className="text-body-sm text-on-surface-variant">Closing {trade.ticker} {STRATEGY_LABEL[trade.strategy]} ${fmtNum(trade.strike, 2)}</span>
          <div>
            <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Outcome</label>
            <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="expired">Expired worthless</option>
              <option value="assigned">Assigned</option>
              <option value="closed_early">Bought to close (BTC)</option>
            </select>
          </div>
          <div>
            <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Close Date</label>
            <input type="date" className={inputCls} value={closeDate} onChange={(e) => setCloseDate(e.target.value)} />
          </div>
          <div>
            <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Cost to Close ($)</label>
            <input type="number" step="0.01" className={inputCls} value={closeCost} onChange={(e) => setCloseCost(e.target.value)} />
          </div>
          <div>
            <label className="text-label-mono text-on-surface-variant uppercase block mb-1">Close Fees ($)</label>
            <input type="number" step="0.01" className={inputCls} value={fees} onChange={(e) => setFees(e.target.value)} />
          </div>
          <button type="button" onClick={onCancel} className="h-9 px-4 rounded-lg border border-outline-variant text-body-sm text-on-surface-variant">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="h-9 px-4 rounded-lg bg-primary text-on-primary text-body-sm font-medium disabled:opacity-50">
            {submitting ? "Saving..." : "Confirm"}
          </button>
        </form>
      </td>
    </tr>
  );
}

// One fetch per row serves the "Stock Price", "Current Delta", and "Current
// Mid" columns (rather than separate components each hitting the
// rate-limited Alpaca-backed endpoint), returning the three <td>s as a
// fragment. Note this one fetch is actually 2 Alpaca requests server-side
// (options snapshot + a separate stock quote -- the underlying price isn't
// included in the options snapshot), so it counts double against the 200/min
// budget per row shown, not per column.
function LiveQuoteCells({ tradeId }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchLiveQuote(tradeId)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", ...data });
      })
      .catch((err) => {
        if (!cancelled) setState({ status: "error", rateLimited: err.body?.error === "rate_limited" });
      });
    return () => {
      cancelled = true;
    };
  }, [tradeId]);

  if (state.status === "loading") {
    const spinner = <span className="material-symbols-outlined text-outline text-sm animate-spin">progress_activity</span>;
    return (
      <>
        <td className="py-cell-padding-y px-cell-padding-x text-right">{spinner}</td>
        <td className="py-cell-padding-y px-cell-padding-x text-right">{spinner}</td>
        <td className="py-cell-padding-y px-cell-padding-x text-right">{spinner}</td>
      </>
    );
  }
  if (state.status === "error") {
    const label = <span className="text-on-surface-variant">{state.rateLimited ? "rate limited" : "--"}</span>;
    return (
      <>
        <td className="py-cell-padding-y px-cell-padding-x text-right">{label}</td>
        <td className="py-cell-padding-y px-cell-padding-x text-right">{label}</td>
        <td className="py-cell-padding-y px-cell-padding-x text-right">{label}</td>
      </>
    );
  }
  return (
    <>
      <td className="py-cell-padding-y px-cell-padding-x text-right font-medium">{state.underlying_price != null ? fmtPrice(state.underlying_price) : "--"}</td>
      <td className="py-cell-padding-y px-cell-padding-x text-right">{state.delta != null ? fmtNum(state.delta, 3) : "--"}</td>
      <td className="py-cell-padding-y px-cell-padding-x text-right">{state.mid != null ? fmtPrice(state.mid) : "--"}</td>
    </>
  );
}

export default function WheelTracker() {
  const [trades, setTrades] = useState(null);
  const [error, setError] = useState(null);
  const [showNewForm, setShowNewForm] = useState(false);
  const [closingId, setClosingId] = useState(null);

  function reload() {
    fetchTrades().then(setTrades).catch(setError);
  }

  useEffect(() => {
    reload();
  }, []);

  const stats = useMemo(() => {
    if (!trades) return null;
    const open = trades.filter((t) => t.status === "open");
    const closed = trades.filter((t) => t.status !== "open");
    const realizedNetPL = closed.reduce((s, t) => s + t.pl, 0);
    const avgDaysClosed = closed.length ? closed.reduce((s, t) => s + (t.days_held || 0), 0) / closed.length : 0;
    return {
      open,
      closed,
      realizedNetPL,
      openPuts: open.filter((t) => t.strategy === "cash_secured_put").length,
      openCalls: open.filter((t) => t.strategy === "covered_call").length,
      avgDaysClosed,
      assignedCount: closed.filter((t) => t.status === "assigned").length,
      expiredCount: closed.filter((t) => t.status === "expired").length,
      btcCount: closed.filter((t) => t.status === "closed_early").length,
    };
  }, [trades]);

  async function handleCreate(payload) {
    await createTrade(payload);
    setShowNewForm(false);
    reload();
  }

  async function handleCloseSubmit(id, updates) {
    await updateTrade(id, updates);
    setClosingId(null);
    reload();
  }

  async function handleDelete(id) {
    if (!confirm("Delete this trade? This can't be undone.")) return;
    await deleteTrade(id);
    reload();
  }

  if (error) {
    return (
      <Layout title="Wheel Tracker">
        <div className="p-6 flex flex-col items-center text-center gap-2">
          <span className="material-symbols-outlined text-danger text-3xl">error</span>
          <p className="text-on-surface-variant text-body-sm max-w-md">
            Couldn't reach the trades API ({error.message}). Is it running? Start it with{" "}
            <code className="font-mono text-on-surface">./scripts/dev_start.sh</code>.
          </p>
        </div>
      </Layout>
    );
  }

  if (!trades || !stats) {
    return <Layout title="Wheel Tracker"><p className="text-on-surface-variant">Loading trades...</p></Layout>;
  }

  return (
    <Layout title="Wheel Tracker">
      <div className="flex items-center justify-between">
        <p className="text-body-sm text-on-surface-variant">Realized P/L, open legs, holding time on closed trades, and how they closed.</p>
        <button
          onClick={() => setShowNewForm((v) => !v)}
          className="h-9 px-4 rounded-lg bg-primary text-on-primary text-body-sm font-medium flex items-center gap-1"
        >
          <span className="material-symbols-outlined text-sm">add</span>
          Add Trade
        </button>
      </div>

      {showNewForm && <NewTradeForm onCreate={handleCreate} onCancel={() => setShowNewForm(false)} />}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Realized Net P/L"
          value={<span className={stats.realizedNetPL >= 0 ? "text-success" : "text-danger"}>{signedPrice(stats.realizedNetPL)}</span>}
          sub="Closed / assigned / expired"
        />
        <StatCard label="Open Positions" value={stats.open.length} sub={`${stats.openPuts} put · ${stats.openCalls} call`} />
        <StatCard label="Avg Days (Closed)" value={fmtNum(stats.avgDaysClosed, 1)} sub="Open -> close" />
        <StatCard label="Close Breakdown" value={`${stats.assignedCount}/${stats.expiredCount}/${stats.btcCount}`} sub="Assigned / Expired / BTC" />
      </div>

      <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
        <div className="p-4 border-b border-surface-container">
          <h3 className="text-headline-sm text-primary">Open Positions</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                <th className="py-cell-padding-y px-cell-padding-x font-normal">Symbol</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Strike</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Qty</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Premium</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">P/L</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Collateral</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-center">Status</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Open</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Delta @ Open</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Stock Price</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Current Delta</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Current Mid</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Exp</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="text-data-tabular">
              {stats.open.length === 0 && (
                <tr><td colSpan={14} className="py-6 text-center text-on-surface-variant text-body-sm">No open positions.</td></tr>
              )}
              {stats.open.map((t) => (
                <Fragment key={t.id}>
                  <tr className="border-b border-surface-container">
                    <td className="py-cell-padding-y px-cell-padding-x">
                      <div className="font-bold text-on-surface">{t.ticker}</div>
                      <span className="inline-block mt-1 px-2 py-0.5 rounded text-[11px] font-medium bg-secondary-container/20 text-secondary">
                        {STRATEGY_LABEL[t.strategy]}
                      </span>
                    </td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.strike)}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{t.qty}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.premium_collected)}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right"><PLBadge value={t.pl} /></td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.collateral_at_risk)}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-center">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${STATUS_CLS[t.status]}`}>{STATUS_LABEL[t.status]}</span>
                    </td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface-variant">{fmtDate(t.open_date)}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">{t.delta_at_open != null ? fmtNum(t.delta_at_open, 3) : "--"}</td>
                    <LiveQuoteCells tradeId={t.id} />
                    <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface-variant">{fmtDate(t.expiration_date)}</td>
                    <td className="py-cell-padding-y px-cell-padding-x text-right">
                      <button onClick={() => setClosingId(closingId === t.id ? null : t.id)} className="text-secondary text-body-sm underline mr-2">
                        Close/Expire
                      </button>
                      <button onClick={() => handleDelete(t.id)} className="text-danger text-body-sm underline">
                        Delete
                      </button>
                    </td>
                  </tr>
                  {closingId === t.id && <CloseTradeForm trade={t} onClose={handleCloseSubmit} onCancel={() => setClosingId(null)} />}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="bg-surface-container-lowest border border-outline-variant rounded-lg flex flex-col">
        <div className="p-4 border-b border-surface-container">
          <h3 className="text-headline-sm text-primary">Closed History</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-surface-container text-label-mono text-on-surface-variant uppercase">
                <th className="py-cell-padding-y px-cell-padding-x font-normal">Symbol</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Strike</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Qty</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Premium</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Open Fees</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Close Cost</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Close Fees</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">P/L</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-center">Status</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Open</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Closed On</th>
                <th className="py-cell-padding-y px-cell-padding-x font-normal text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="text-data-tabular">
              {stats.closed.length === 0 && (
                <tr><td colSpan={12} className="py-6 text-center text-on-surface-variant text-body-sm">No closed trades yet.</td></tr>
              )}
              {stats.closed.map((t) => (
                <tr key={t.id} className="border-b border-surface-container">
                  <td className="py-cell-padding-y px-cell-padding-x">
                    <div className="font-bold text-on-surface">{t.ticker}</div>
                    <span className="inline-block mt-1 px-2 py-0.5 rounded text-[11px] font-medium bg-secondary-container/20 text-secondary">
                      {STRATEGY_LABEL[t.strategy]}
                    </span>
                  </td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.strike)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{t.qty}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.premium_collected)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.open_fees)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.close_cost)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">{fmtPrice(t.fees)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right"><PLBadge value={t.pl} /></td>
                  <td className="py-cell-padding-y px-cell-padding-x text-center">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${STATUS_CLS[t.status]}`}>{STATUS_LABEL[t.status]}</span>
                  </td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface-variant">{fmtDate(t.open_date)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right text-on-surface-variant">{fmtDate(t.close_date)}</td>
                  <td className="py-cell-padding-y px-cell-padding-x text-right">
                    <button onClick={() => handleDelete(t.id)} className="text-danger text-body-sm underline">
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Layout>
  );
}
