#!/usr/bin/env bash
# Per-run setup: reset the qualification workspace and start the local intake stub.
set -u
SINK_DIR=/tmp/acme-sink-resource-disk-fill
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf "$SINK_DIR" /tmp/acme-resource-disk-fill
mkdir -p "$SINK_DIR" /tmp/acme-resource-disk-fill
: > /tmp/run-hooks.log
rm -f /tmp/output.md
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9537 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
exit 0
