#!/usr/bin/env bash
# Gracefully stops the React/Vite dashboard started by scripts/ui_start.sh.
# Sends SIGTERM, waits, then SIGKILL if it's still alive, so nothing is left dangling.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.ui.pid"
PORT=5173

stop_pid() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi

  echo "Stopping UI dev server (PID $pid)..."
  kill -TERM "$pid" 2>/dev/null

  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.5
  done

  echo "Still running -- sending SIGKILL to PID $pid"
  kill -KILL "$pid" 2>/dev/null
  sleep 0.5
  return 0
}

stopped_any=0

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if stop_pid "$pid"; then
    stopped_any=1
  fi
  rm -f "$PID_FILE"
fi

# Vite/esbuild can leave a child bound to the port even if the tracked PID exited
# (e.g. crash-restarted dev server). Clean up anything still listening on it.
leftover_pids="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
if [[ -n "$leftover_pids" ]]; then
  echo "Killing leftover process(es) on port $PORT: $leftover_pids"
  kill -TERM $leftover_pids 2>/dev/null
  sleep 1
  leftover_pids="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
  if [[ -n "$leftover_pids" ]]; then
    kill -KILL $leftover_pids 2>/dev/null
  fi
  stopped_any=1
fi

if [[ "$stopped_any" -eq 1 ]]; then
  echo "UI dev server stopped."
else
  echo "No UI dev server was running."
fi
