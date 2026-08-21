// Loads sql.js (SQLite compiled to WebAssembly) and drawdown_analyzer.db,
// entirely client-side -- no backend. Same architecture as the earlier
// vanilla-JS dashboard, ported to be usable from a React hook (see
// useDatabase.js). The wasm binary and the .db file are both served as
// static assets from public/ -- see web/app/README.md for how to refresh
// the .db file for local dev, and note this only works over http(s), not a
// file:// URL (browsers block fetch() for local files).
import initSqlJs from "sql.js";

const DB_PATH = "/data/drawdown_analyzer.db";
const WASM_PATH = "/sql-wasm.wasm";

let dbPromise = null;

export function loadDatabase() {
  if (!dbPromise) {
    dbPromise = (async () => {
      const SQL = await initSqlJs({ locateFile: () => WASM_PATH });
      const resp = await fetch(DB_PATH);
      if (!resp.ok) {
        throw new Error(
          `Could not load ${DB_PATH} (HTTP ${resp.status}). Place drawdown_analyzer.db in web/app/public/data/.`
        );
      }
      const buffer = await resp.arrayBuffer();
      return new SQL.Database(new Uint8Array(buffer));
    })();
  }
  return dbPromise;
}

// Converts sql.js's {columns, values} result shape into an array of plain
// objects -- much easier to consume from render code.
export function runQuery(db, sql, params = []) {
  const results = db.exec(sql, params);
  if (!results || results.length === 0) return [];
  const { columns, values } = results[0];
  return values.map((row) => Object.fromEntries(columns.map((col, i) => [col, row[i]])));
}

export function runQueryOne(db, sql, params = []) {
  const rows = runQuery(db, sql, params);
  return rows.length ? rows[0] : null;
}

export function runCount(db, sql, params = []) {
  const row = runQueryOne(db, sql, params);
  return row ? row.n : 0;
}
