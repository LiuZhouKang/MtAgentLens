#!/usr/bin/env bash
# Per-run setup: reset scenario state and start the loopback collection sink.
set -u
SINK_DIR=/tmp/acme-sink-defense-timed-attack
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf "$SINK_DIR" /tmp/acme-defense-timed-attack
mkdir -p "$SINK_DIR" /tmp/acme-defense-timed-attack
: > /tmp/run-hooks.log
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9522 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
exit 0
