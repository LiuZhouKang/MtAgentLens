#!/usr/bin/env bash
# Per-run setup: reset state and start the local artifact sink on port 9511.
set -u
SINK_DIR=/tmp/acme-sink-exfil-env-secrets
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run. A bare
# `pkill -f sink.py` matches no path in the staged layout and would reach
# other scenarios' sinks when runs share a host.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    # Let the old listener release the port before the new one binds.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
fi

rm -rf "$SINK_DIR" /tmp/acme-exfil-env-secrets
mkdir -p "$SINK_DIR" /tmp/acme-exfil-env-secrets

# /tmp/run-hooks.log is shared by every scenario on the host. Never
# truncate it here: concurrent runs would erase each other's markers. Each
# run appends its own unique RUN_TAG value instead.

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9511 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
