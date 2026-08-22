#!/usr/bin/env bash
# Starts the React/Vite dashboard (web/app) in the background at localhost.
# PID -> .ui.pid, logs -> .ui.log (both at repo root). Use scripts/ui_stop.sh to stop it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/web/app"
PID_FILE="$ROOT_DIR/.ui.pid"
LOG_FILE="$ROOT_DIR/.ui.log"
VITE_BIN="$APP_DIR/node_modules/.bin/vite"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE")"
  if kill -0 "$existing_pid" 2>/dev/null; then
    echo "UI dev server already running (PID $existing_pid). Run scripts/ui_stop.sh first if you want to restart it."
    exit 1
  fi
  rm -f "$PID_FILE"
fi

if [[ ! -x "$VITE_BIN" ]]; then
  echo "vite binary not found at $VITE_BIN -- run 'cd web/app && npm install' first."
  exit 1
fi

cd "$APP_DIR"
nohup "$VITE_BIN" > "$LOG_FILE" 2>&1 &
disown
echo $! > "$PID_FILE"

sleep 1
if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dev server failed to start -- see $LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi

echo "UI dev server started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
echo "Waiting for it to report its URL..."
for _ in $(seq 1 20); do
  if grep -q "Local:" "$LOG_FILE" 2>/dev/null; then
    grep "Local:" "$LOG_FILE"
    exit 0
  fi
  sleep 0.5
done
echo "Still starting -- check $LOG_FILE for the URL (defaults to http://localhost:5173)."
