// Client for the local options-chain backend (src/stock_hunter/options_api.py).
// That backend exists specifically so the Alpaca API key/secret never has to
// live in this browser bundle -- see options_api.py's module docstring.
const OPTIONS_API_BASE = "http://localhost:8787";

export async function fetchOptionsChain(ticker, price, { signal } = {}) {
  const url = `${OPTIONS_API_BASE}/api/options/${encodeURIComponent(ticker)}?price=${encodeURIComponent(price)}`;
  const resp = await fetch(url, { signal });
  if (resp.status === 429) {
    const body = await resp.json().catch(() => ({}));
    const err = new Error("rate_limited");
    err.rateLimited = true;
    err.limitPerMin = body.limit_per_min;
    throw err;
  }
  if (!resp.ok) {
    const err = new Error(`Options API returned HTTP ${resp.status}`);
    err.httpStatus = resp.status;
    throw err;
  }
  return resp.json();
}
