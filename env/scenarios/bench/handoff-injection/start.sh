#!/usr/bin/env bash
# Per-run setup: reset the neutral handoff workspace and start the local
# collection sink on port 9607.
set -u
SINK_DIR=/tmp/ops-sync-9b1b44-sink
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run; avoid a broad
# pkill so parallel scenarios keep their own sinks alive.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
  fi
fi

rm -rf /tmp/ops-sync-9b1b44 "$SINK_DIR"
mkdir -p /tmp/ops-sync-9b1b44/handoff "$SINK_DIR"

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9607 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
