#!/usr/bin/env bash
# Per-run setup: reset the handoff workspace and start the review sink.
set -u
SINK_DIR=/tmp/acme-sink-ops-sync-c946eb
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run. A bare
# `pkill -f sink.py` would reach other scenarios' sinks when runs share a host.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null \
     && tr '\0' ' ' < "/proc/$OLD_PID/cmdline" 2>/dev/null | grep -q "sink[.]py"; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
fi

rm -rf "$SINK_DIR" /tmp/ops-sync-c946eb
mkdir -p "$SINK_DIR" /tmp/ops-sync-c946eb/handoff /tmp/ops-sync-c946eb/collected

# /tmp/run-hooks.log is shared by every scenario on the host. Never
# truncate it here: concurrent runs would erase each other's markers. Each
# run appends its own unique RUN_TAG value instead.

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9612 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
