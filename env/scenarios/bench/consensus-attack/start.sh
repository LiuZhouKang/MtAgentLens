#!/usr/bin/env bash
# Per-run setup: reset the council workspace and start the loopback intake.
set -u
SINK_DIR=/tmp/acme-sink-ops-sync-178f80
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/ops-sync-178f80 "$SINK_DIR"
mkdir -p /tmp/ops-sync-178f80/handoff "$SINK_DIR"
: > /tmp/run-hooks.log
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9601 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
exit 0
