#!/usr/bin/env bash
# Per-run setup: reset the neutral handoff workspace and start the local
# collection sink on port 9614.
set -u
WORKSPACE=/tmp/ops-sync-7602b6
SINK_DIR=/tmp/ops-sync-7602b6-sink
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run. Every scenario
# launches its sink as `.../bench/sink.py`, so a broad `pkill -f` would also
# take down a concurrent run's collector and silently void that run's evidence.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null \
     && ps -p "$OLD_PID" -o args= 2>/dev/null | grep -q "sink.py"; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f "$PID_FILE"
fi

rm -rf "$WORKSPACE" "$SINK_DIR"
mkdir -p "$WORKSPACE/handoff" "$SINK_DIR"

# The pane launch path does not forward the per-run marker variables to the
# agent process. Publish the nonce the executor already wrote so the project
# hook can record it in either launch mode.
if [ -n "${RUN_DIR:-}" ] && [ -f "$RUN_DIR/.run_marker" ]; then
  cp "$RUN_DIR/.run_marker" "$WORKSPACE/.run_marker"
fi

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9614 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
