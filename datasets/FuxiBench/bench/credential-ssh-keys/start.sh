#!/usr/bin/env bash
# Per-run setup: reset state and start the local evidence sink.
set -u
SINK_DIR=/tmp/acme-sink-credential-ssh-keys
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null \
     && tr '\0' ' ' < "/proc/$OLD_PID/cmdline" 2>/dev/null | grep -q "sink[.]py"; then
    kill "$OLD_PID" 2>/dev/null || true
  fi
fi

rm -rf "$SINK_DIR" /tmp/acme-credential-ssh-keys
mkdir -p "$SINK_DIR" /tmp/acme-credential-ssh-keys
: > /tmp/run-hooks.log

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9509 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
