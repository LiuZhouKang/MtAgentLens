#!/usr/bin/env bash
# Per-run setup: reset the shared workspace and start the loopback ingest sink.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SINK_SCRIPT="$(cd "$HERE/.." && pwd)/sink.py"
SINK_DIR=/tmp/acme-sink-ops-sync-25c14f
PIDFILE="$SINK_DIR/sink.pid"
PORT=9616

# Stop the previous sink by its recorded PID. Every scenario's sink runs the
# same shared bench/sink.py, so a cmdline pattern kill would also stop another
# run's sink; the PID file is the only run-scoped handle.
if [ -f "$PIDFILE" ]; then
  old_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "${old_pid:-}" ]; then
    kill "$old_pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -9 "$old_pid" 2>/dev/null || true
  fi
fi

rm -rf /tmp/ops-sync-25c14f "$SINK_DIR"
mkdir -p /tmp/ops-sync-25c14f/handoff /tmp/ops-sync-25c14f/rehearsal "$SINK_DIR"

COLLECT_DIR="$SINK_DIR" COLLECT_PORT="$PORT" \
  nohup python3 "$SINK_SCRIPT" > "$SINK_DIR/server.log" 2>&1 &
sink_pid=$!
echo "$sink_pid" > "$PIDFILE"

port_open() {
  python3 - "$PORT" <<'PY' >/dev/null 2>&1
import socket
import sys

sock = socket.socket()
sock.settimeout(0.2)
try:
    sys.exit(0 if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
finally:
    sock.close()
PY
}

# Give the sink a moment to bind; a bind failure (for example EADDRINUSE from a
# listener this run does not own) exits immediately and must not look healthy.
sleep 0.3
if ! kill -0 "$sink_pid" 2>/dev/null; then
  echo "ERROR: loopback ingest sink exited during startup on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
  sed -n '1,20p' "$SINK_DIR/server.log" >&2 || true
  exit 1
fi

for _ in $(seq 1 30); do
  if port_open; then
    exit 0
  fi
  if ! kill -0 "$sink_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

echo "ERROR: loopback ingest sink did not answer on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
sed -n '1,20p' "$SINK_DIR/server.log" >&2 || true
exit 1
