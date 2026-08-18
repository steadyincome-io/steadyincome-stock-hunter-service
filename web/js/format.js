// Formatting helpers -- mirrors ui/app.py's _format_* helpers so numbers
// read the same way as the Streamlit dashboard (e.g. "$1.2B" not "1234500000").

function fmtNum(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(decimals);
}

function fmtPct(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Number(value).toFixed(decimals)}%`;
}

function fmtMoney(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toFixed(decimals)}`;
}

// Auto-scales a raw dollar figure (e.g. revenue_usd) to K/M/B/T, same idea
// as ui/app.py's _format_money_auto.
function fmtMoneyAuto(value, decimals = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(decimals)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(decimals)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(decimals)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(decimals)}K`;
  return `${sign}$${abs.toFixed(decimals)}`;
}

function fmtDate(value) {
  if (!value) return "—";
  return String(value).slice(0, 19).replace("T", " ");
}

function fmtInt(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return String(Math.round(Number(value)));
}

// Score badge (0-100) -- green/amber/red, same thresholds as ui/app.py's
// _badge_class_for_score. invert=true for scores where LOWER is better (risk).
function scoreBadgeClass(score, invert = false) {
  if (score === null || score === undefined || Number.isNaN(score)) return "badge-neutral";
  const s = invert ? 100 - score : score;
  if (s >= 65) return "badge-good";
  if (s >= 40) return "badge-mid";
  return "badge-bad";
}

function riskLevelBadgeClass(level) {
  if (!level) return "badge-neutral";
  const l = String(level).toLowerCase();
  if (l.includes("material") || l.includes("severe") || l.includes("high")) return "badge-bad";
  if (l.includes("moderate") || l.includes("elevated")) return "badge-mid";
  if (l.includes("low") || l.includes("minimal") || l.includes("healthy")) return "badge-good";
  return "badge-neutral";
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
