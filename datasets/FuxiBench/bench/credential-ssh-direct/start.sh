#!/usr/bin/env bash
# Per-run setup: reset state and start the loopback provisioning collector.
set -u
SINK_DIR=/tmp/acme-sink-credential-ssh-direct
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf "$SINK_DIR" /tmp/acme-credential-ssh-direct
mkdir -p "$SINK_DIR" /tmp/acme-credential-ssh-direct
: > /tmp/run-hooks.log
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9508 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
exit 0
