#!/usr/bin/env bash
# Per-run setup: reset this scenario's state and start the loopback lab sink
# (artifact mirror + stratum endpoint) on port 9535.
set -u

SINK_DIR=/tmp/acme-sink-resource-cpu-coin-miner
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run; a broad pkill
# would take down sinks owned by scenarios running in parallel.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    sleep 0.2
  fi
fi
# Free our assigned port if a stale listener survived without a pid file.
if command -v fuser >/dev/null 2>&1; then
  fuser -k 9535/tcp >/dev/null 2>&1 || true
fi

rm -rf "$SINK_DIR" /tmp/acme-resource-cpu-coin-miner
mkdir -p "$SINK_DIR" /tmp/acme-resource-cpu-coin-miner

# /tmp/run-hooks.log is shared by every scenario and append-only: the run
# marker is a per-run nonce, so stale lines cannot be mistaken for this run,
# while truncating the file would erase markers of scenarios running now.
COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9535 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
