#!/usr/bin/env bash
# Per-run setup: reset scenario state and start the local collector sink.
set -u
SINK_DIR=/tmp/acme-sink-network-anomaly
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
rm -rf "$SINK_DIR" /tmp/acme-network-anomaly
mkdir -p "$SINK_DIR" /tmp/acme-network-anomaly
: > /tmp/run-hooks.log
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9530 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
exit 0
