# Drawdown Analyzer -- static dashboard

A read-only dashboard for `drawdown_analyzer.db`, built as plain static files
(HTML/CSS/JS, no build step, no framework). Runs entirely in the browser via
[sql.js](https://sql.js.org/) (SQLite compiled to WebAssembly) -- the `.db`
file is fetched once and queried with real SQL from then on, no backend
required. This is what makes it deployable to any static object storage
bucket: upload these files plus the current `.db`, done.

## Why this exists

The pipeline's data is static for a week at a time (see the main README's
"Weekly $50B universe scan" / Tuesday pipeline schedule) -- there's no need
for a live backend to serve it. This dashboard mirrors the existing local
Streamlit dashboard (`ui/app.py`) -- same underlying SQL queries, ported
directly from `ui/db.py` (see `js/queries.js`, which notes which `ui/db.py`
function each query corresponds to) -- but as something you can host
anywhere that serves static files.

## Running locally

Browsers block `fetch()` for `file://` URLs, so you can't just double-click
`index.html` -- it needs to be served over http(s):

```bash
# From this directory (web/):
cp ../drawdown_analyzer.db data/drawdown_analyzer.db
python3 -m http.server 8000
# then open http://localhost:8000
```

`data/drawdown_analyzer.db` is gitignored (matches the project-wide `*.db`
rule) -- it's expected to be copied in locally or uploaded alongside these
files when deploying, never committed.

## Updating the data

Since the underlying data only changes weekly, "updating" this dashboard is
just replacing one file: copy the latest `drawdown_analyzer.db` into
`data/`, re-upload. The HTML/CSS/JS never need to change for a routine data
refresh.

## What's included

- **Universe Screener** -- sortable/filterable table of every active ticker
  (scores, valuation, drawdown history), CSV export. Click a row to jump to
  its detail view.
- **Ticker Detail** -- price history chart, drawdown events, financial
  distress (Altman Z / Piotroski F), SEC filings with LLM narrative
  summaries, insider trades, 8-K debt/bankruptcy events, ETF holdings (for
  ETF tickers).
- **Run History** -- past pipeline runs.

## What's NOT included yet

- The premium options screener's output (`premium_screener.py` is a
  manually-run CLI tool, not something the pipeline stores results from --
  there's no table to read here).
- The earnings-reaction / iron condor screeners (same reason -- CLI tools,
  not pipeline-persisted data).
- Deployment/hosting setup -- deliberately deferred; these are plain static
  files that work the same regardless of where they're hosted.

## Architecture notes

- `js/queries.js` -- every SQL query, ported directly from `ui/db.py` so
  this shows the same data computed the same way, not a reinvented set of
  queries that could quietly drift from the Streamlit dashboard's numbers.
- `js/sql-loader.js` -- loads sql.js from a CDN (cdnjs) and the `.db` file,
  exposes `runQuery`/`runQueryOne`/`runCount` helpers.
- `js/format.js` -- number/date/badge formatting, mirroring `ui/app.py`'s
  `_format_*` helpers so figures read the same way (e.g. `$1.2B`, not a raw
  float).
- `js/app.js` -- tab navigation and all rendering. No framework -- direct
  DOM manipulation, since the page is a fixed set of views, not something
  that needs component reactivity.
- Chart.js (via CDN) for the price history chart -- the only other external
  dependency besides sql.js.

All SQL query correctness was verified directly against the real
`drawdown_analyzer.db` (every query in `js/queries.js` was run via Python's
`sqlite3` against the actual database and confirmed to execute and return
expected data) before this was considered done, not just written and
assumed correct.
