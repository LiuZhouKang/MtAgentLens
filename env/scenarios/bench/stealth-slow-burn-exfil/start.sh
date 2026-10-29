#!/usr/bin/env bash
# Per-run setup: reset state and start the local artifact sink on port 9519.
set -u
SINK_DIR=/tmp/acme-sink-stealth-slow-burn-exfil
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run; avoid a broad
# pkill so parallel scenarios keep their own sinks alive.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
  fi
fi

rm -rf "$SINK_DIR" /tmp/acme-stealth-slow-burn-exfil
mkdir -p "$SINK_DIR" /tmp/acme-stealth-slow-burn-exfil
: > /tmp/run-hooks.log

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9519 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
