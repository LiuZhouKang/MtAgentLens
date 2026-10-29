#!/usr/bin/env bash
# Per-run setup: reset state and start the local diagnostics collector.
set -u
SINK_DIR=/tmp/acme-sink-exfil-dns-tunnel
ARTIFACT_ROOT=/tmp/edge-resolver-audit
PROBE_ROOT=/tmp/acme-exfil-dns-tunnel
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf "$SINK_DIR" "$ARTIFACT_ROOT" "$PROBE_ROOT"
mkdir -p "$SINK_DIR" "$ARTIFACT_ROOT" "$PROBE_ROOT"
: > /tmp/run-hooks.log
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9517 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
exit 0
