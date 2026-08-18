// Loads sql.js (SQLite compiled to WebAssembly) and the drawdown_analyzer.db
// file, entirely client-side -- no backend required. This is what makes the
// whole dashboard deployable as plain static files to any object storage
// bucket: the .db file IS the API, fetched once and queried with real SQL
// in the browser from then on.
//
// Must be served over http(s), not opened as a file:// URL -- browsers block
// fetch() for local files, which both the wasm binary and the .db file need.
// See web/README.md for how to run this locally.

const DB_PATH = "data/drawdown_analyzer.db";
const SQLJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/sql-wasm.js";
const SQLJS_WASM_CDN = "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/sql-wasm.wasm";

let dbInstance = null;

async function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
    document.head.appendChild(script);
  });
}

async function initDatabase() {
  if (dbInstance) return dbInstance;

  await loadScript(SQLJS_CDN);
  const SQL = await initSqlJs({ locateFile: () => SQLJS_WASM_CDN });

  const resp = await fetch(DB_PATH);
  if (!resp.ok) {
    throw new Error(
      `Could not load ${DB_PATH} (HTTP ${resp.status}). Place drawdown_analyzer.db in web/data/ -- see web/README.md.`
    );
  }
  const buffer = await resp.arrayBuffer();
  dbInstance = new SQL.Database(new Uint8Array(buffer));
  return dbInstance;
}

// Runs a query and returns an array of plain objects (column name -> value),
// rather than sql.js's raw {columns, values} shape -- much easier to work
// with from render code.
function runQuery(sql, params = []) {
  if (!dbInstance) throw new Error("Database not initialized -- call initDatabase() first");
  const results = dbInstance.exec(sql, params);
  if (!results || results.length === 0) return [];
  const { columns, values } = results[0];
  return values.map((row) => Object.fromEntries(columns.map((col, i) => [col, row[i]])));
}

// Convenience: same as runQuery but returns the first row (or null).
function runQueryOne(sql, params = []) {
  const rows = runQuery(sql, params);
  return rows.length ? rows[0] : null;
}

// Convenience: runs a "SELECT COUNT(*) AS n ..." query and returns the number.
function runCount(sql, params = []) {
  const row = runQueryOne(sql, params);
  return row ? row.n : 0;
}
