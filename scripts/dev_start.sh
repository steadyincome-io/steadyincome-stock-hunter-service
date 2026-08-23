#!/usr/bin/env bash
# Starts the local dev environment for the web UI: the options-chain API
# (src/stock_hunter/options_api.py, port 8787) and the React/Vite dashboard
# (web/app, port 5173). Both are backgrounded; PIDs/logs live at repo root
# (.api.pid/.api.log, .ui.pid/.ui.log). Use scripts/dev_stop.sh to stop both.
#
# Safe to re-run: a service that's already up is left alone and reported as
# such rather than treated as an error, so this can be called repeatedly.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/web/app"
API_PID_FILE="$ROOT_DIR/.api.pid"
API_LOG_FILE="$ROOT_DIR/.api.log"
API_PORT="${OPTIONS_API_PORT:-8787}"
UI_PID_FILE="$ROOT_DIR/.ui.pid"
UI_LOG_FILE="$ROOT_DIR/.ui.log"
VITE_BIN="$APP_DIR/node_modules/.bin/vite"

start_api() {
  if [[ -f "$API_PID_FILE" ]] && kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; then
    echo "Options API already running (PID $(cat "$API_PID_FILE"))."
    return 0
  fi
  rm -f "$API_PID_FILE"

  cd "$ROOT_DIR"
  if [[ -f "venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
  fi

  nohup env PYTHONPATH=src python3 -m stock_hunter.options_api > "$API_LOG_FILE" 2>&1 &
  disown
  echo $! > "$API_PID_FILE"

  sleep 1
  if ! kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; then
    echo "Options API failed to start -- see $API_LOG_FILE"
    rm -f "$API_PID_FILE"
    return 1
  fi
  echo "Options API started (PID $(cat "$API_PID_FILE")) on http://localhost:$API_PORT. Logs: $API_LOG_FILE"
}

start_ui() {
  if [[ -f "$UI_PID_FILE" ]] && kill -0 "$(cat "$UI_PID_FILE")" 2>/dev/null; then
    echo "UI dev server already running (PID $(cat "$UI_PID_FILE"))."
    return 0
  fi
  rm -f "$UI_PID_FILE"

  if [[ ! -x "$VITE_BIN" ]]; then
    echo "vite binary not found at $VITE_BIN -- run 'cd web/app && npm install' first."
    return 1
  fi

  cd "$APP_DIR"
  nohup "$VITE_BIN" > "$UI_LOG_FILE" 2>&1 &
  disown
  echo $! > "$UI_PID_FILE"

  sleep 1
  if ! kill -0 "$(cat "$UI_PID_FILE")" 2>/dev/null; then
    echo "UI dev server failed to start -- see $UI_LOG_FILE"
    rm -f "$UI_PID_FILE"
    return 1
  fi

  echo "UI dev server started (PID $(cat "$UI_PID_FILE"))."
  for _ in $(seq 1 20); do
    if grep -q "Local:" "$UI_LOG_FILE" 2>/dev/null; then
      grep "Local:" "$UI_LOG_FILE"
      return 0
    fi
    sleep 0.5
  done
  echo "Still starting -- check $UI_LOG_FILE for the URL (defaults to http://localhost:5173)."
}

status=0
start_api || status=1
start_ui || status=1
exit $status
