import { useEffect, useRef, useState } from "react";
import { loadDatabase, runQuery, runQueryOne } from "./sqlLoader";

// Loads the sql.js database once and exposes it plus small query helpers
// bound to it, so components don't need to juggle the db instance directly.
export function useDatabase() {
  const [db, setDb] = useState(null);
  const [error, setError] = useState(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    loadDatabase()
      .then((loaded) => {
        if (mounted.current) setDb(loaded);
      })
      .catch((err) => {
        if (mounted.current) setError(err);
      });
    return () => {
      mounted.current = false;
    };
  }, []);

  return {
    db,
    loading: !db && !error,
    error,
    query: (sql, params) => runQuery(db, sql, params),
    queryOne: (sql, params) => runQueryOne(db, sql, params),
  };
}
