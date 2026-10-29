#!/usr/bin/env bash
# Per-run setup: reset the station workspace and start the loopback relay sink.
#
# The sink is per scenario. The pidfile lets a later run stop only the process
# this scenario started, the port check refuses to touch a listener owned by
# another run, and the script exits non-zero when the relay does not come up so
# a run with no relay is not reported as a success.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SINK_SCRIPT="$HERE/../sink.py"
WORKSPACE=/tmp/ops-sync-e29a5e
STATE_DIR=/tmp/ops-relay-state-e29a5e
PID_FILE="$STATE_DIR/relay.pid"
PORT=9608

fail() {
  echo "start.sh: $*" >&2
  exit 1
}

[ -f "$SINK_SCRIPT" ] || fail "sink script missing: $SINK_SCRIPT"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

mkdir -p "$STATE_DIR"

# Stop only the relay this scenario started on a previous run. A blanket pkill
# would take down the relays of scenarios running in parallel.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f "$PID_FILE"
fi

port_busy() {
  python3 - "$1" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.5)
busy = sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0
sock.close()
raise SystemExit(0 if busy else 1)
PY
}

if port_busy "$PORT"; then
  fail "port $PORT is already in use; refusing to start a second relay"
fi

rm -rf "$WORKSPACE" "$STATE_DIR/collect"
mkdir -p "$WORKSPACE/handoff"

COLLECT_DIR="$STATE_DIR/collect" COLLECT_PORT="$PORT" \
  nohup python3 "$SINK_SCRIPT" > "$STATE_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"

READY=0
for _ in $(seq 1 50); do
  if port_busy "$PORT"; then
    READY=1
    break
  fi
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

if [ "$READY" -ne 1 ]; then
  echo "start.sh: relay did not become ready on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
  if [ -s "$STATE_DIR/server.log" ]; then
    tail -n 40 "$STATE_DIR/server.log" >&2
  fi
  rm -f "$PID_FILE"
  exit 1
fi

echo "start.sh: relay listening on ${COLLECT_HOST:-127.0.0.1}:$PORT (pid $(cat "$PID_FILE"))"
