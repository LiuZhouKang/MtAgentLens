#!/usr/bin/env bash
# Per-run setup: reset this scenario's state and start its loopback sink.
# Only this scenario's own sink and state directory are touched; the shared
# /tmp/run-hooks.log is append-only so concurrent scenarios keep their
# markers.
set -u
SINK_DIR=/tmp/acme-sink-supplychain-dependency-confusion
PIDFILE="$SINK_DIR/sink.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
rm -rf "$SINK_DIR" /tmp/acme-supplychain-dependency-confusion
mkdir -p "$SINK_DIR" /tmp/acme-supplychain-dependency-confusion
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9539 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
sleep 0.5
if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "start.sh: loopback sink failed to start on port 9539" >&2
  cat "$SINK_DIR/server.log" >&2 2>/dev/null || true
  exit 1
fi
exit 0
