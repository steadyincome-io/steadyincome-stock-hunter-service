export function fmtPrice(v) {
  if (v === null || v === undefined) return "--";
  return `$${Number(v).toFixed(2)}`;
}

export function fmtPct(v, digits = 1) {
  if (v === null || v === undefined) return "--";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

export function fmtNum(v, digits = 1) {
  if (v === null || v === undefined) return "--";
  return Number(v).toFixed(digits);
}

// v is a raw USD figure (e.g. sec_financials revenue_usd/net_income_usd).
export function fmtCompact(v) {
  if (v === null || v === undefined) return "--";
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
}

// v is already in $B, per universe.market_cap's storage convention
// (universe_scanner.py: `round(market_cap_usd / 1e9, 1)`).
export function fmtMarketCapB(v) {
  if (v === null || v === undefined) return "--";
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1000) return `$${(n / 1000).toFixed(1)}T`;
  return `$${n.toFixed(1)}B`;
}

export function fmtDate(v) {
  if (!v) return "--";
  return String(v).slice(0, 10);
}

export function changeColorClass(v) {
  if (v === null || v === undefined) return "text-on-surface-variant";
  return Number(v) >= 0 ? "text-success" : "text-danger";
}

// distress_analytics.py emits exactly these risk_level strings (plus
// "Insufficient data" when the underlying financials are missing).
export function riskLevelChip(level) {
  if (!level) return { label: "Unknown", cls: "bg-outline-variant/30 text-on-surface-variant" };
  const l = String(level).toLowerCase();
  if (l.includes("solvency")) return { label: "Distress", cls: "bg-[#7f1d1d]/10 text-[#7f1d1d]" };
  if (l.includes("elevated")) return { label: "Elevated", cls: "bg-danger/10 text-danger" };
  if (l.includes("low")) return { label: "Low", cls: "bg-success/10 text-success" };
  if (l.includes("insufficient")) return { label: "Unscored", cls: "bg-outline-variant/30 text-on-surface-variant" };
  return { label: level, cls: "bg-info/10 text-info" };
}

export function verdictChip(verdict) {
  if (!verdict) return { label: "N/A", cls: "bg-outline-variant/30 text-on-surface-variant" };
  const v = String(verdict).toLowerCase();
  if (v.includes("buy")) return { label: verdict, cls: "bg-success/10 text-success" };
  if (v.includes("sell") || v.includes("avoid")) return { label: verdict, cls: "bg-danger/10 text-danger" };
  if (v.includes("hold") || v.includes("watch")) return { label: verdict, cls: "bg-warning/10 text-warning" };
  return { label: verdict, cls: "bg-info/10 text-info" };
}
