#!/usr/bin/env bash
# Gracefully stops both dev services started by scripts/dev_start.sh: the
# options API (port 8787) and the Vite UI dev server (port 5173). Sends
# SIGTERM, waits, then SIGKILL if still alive, then cleans up anything still
# bound to either port -- so nothing is left dangling either way.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PID_FILE="$ROOT_DIR/.api.pid"
API_PORT="${OPTIONS_API_PORT:-8787}"
UI_PID_FILE="$ROOT_DIR/.ui.pid"
UI_PORT=5173

stop_pid() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
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

stop_service() {
  local name="$1" pid_file="$2" port="$3"
  local stopped=0

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if stop_pid "$pid"; then
      echo "$name stopped (was PID $pid)."
      stopped=1
    fi
    rm -f "$pid_file"
  fi

  local leftover
  leftover="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [[ -n "$leftover" ]]; then
    echo "Killing leftover $name process(es) on port $port: $leftover"
    kill -TERM $leftover 2>/dev/null
    sleep 1
    leftover="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    [[ -n "$leftover" ]] && kill -KILL $leftover 2>/dev/null
    stopped=1
  fi

  if [[ "$stopped" -eq 0 ]]; then
    echo "$name was not running."
  fi
}

stop_service "Options API" "$API_PID_FILE" "$API_PORT"
stop_service "UI dev server" "$UI_PID_FILE" "$UI_PORT"
