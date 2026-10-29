#!/usr/bin/env bash
# Per-run setup: reset state and start the loopback integration stub.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SINK_DIR=/tmp/acme-sink-resource-direct
PIDFILE="$SINK_DIR/sink.pid"
PORT=9536
port_hex="$(printf '%04X' "$PORT")"

# Resolve the sink for both layouts: staged (<bundle>/bench/start.sh with
# <bundle>/sink.py one level up) and installed (scenarios/bench/<id>/start.sh
# with the shared scenarios/bench/sink.py one level up).
SINK=""
for candidate in "$SCRIPT_DIR/sink.py" "$SCRIPT_DIR/../sink.py"; do
  if [ -f "$candidate" ]; then
    SINK="$candidate"
    break
  fi
done
if [ -z "$SINK" ]; then
  echo "setup failed: no sink.py beside $SCRIPT_DIR or its parent" >&2
  exit 1
fi

port_listening() {
  awk -v p=":$port_hex" \
    '$4 == "0A" && substr($2, length($2) - 4) == p { found = 1 } END { exit(found ? 0 : 1) }' \
    /proc/net/tcp 2>/dev/null
}

# Stop a previous run's stub: first the recorded pid, then any sink process that
# carries this run's sink directory in its environment (a run whose pidfile was
# lost, or a crash between start and cleanup). Nothing else is signalled.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && [ -r "/proc/$OLD_PID/cmdline" ] &&
     tr '\0' ' ' < "/proc/$OLD_PID/cmdline" | grep -q "sink.py"; then
    kill "$OLD_PID" 2>/dev/null || true
  fi
fi
for pid in $(pgrep -f "sink.py" 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -qx "COLLECT_DIR=$SINK_DIR"; then
    kill "$pid" 2>/dev/null || true
  fi
done
for _ in $(seq 1 20); do
  port_listening || break
  sleep 0.1
done
if port_listening; then
  echo "setup failed: ${COLLECT_HOST:-127.0.0.1}:$PORT is held by a process this run does not own" >&2
  exit 1
fi

rm -rf "$SINK_DIR" /tmp/acme-resource-direct
mkdir -p "$SINK_DIR" /tmp/acme-resource-direct
# /tmp/run-hooks.log is shared and append-only across scenarios: another
# run's setup may hold a nonce there, so this script never truncates it. The
# run's own nonce makes stale lines distinguishable.

COLLECT_DIR="$SINK_DIR" COLLECT_PORT="$PORT" \
  nohup python3 "$SINK" > "$SINK_DIR/server.log" 2>&1 &
SINK_PID=$!
echo "$SINK_PID" > "$PIDFILE"

# A silent nohup failure must abort the run: pre-run success is only reported
# when our own process is still running and owns the listening socket. Checking
# only the port would also accept a foreign listener, a leftover sink from an
# earlier run, or a child that died before it ever bound.
owns_port() {
  local inode fd target
  for inode in $(awk -v p=":$port_hex" \
      '$4 == "0A" && substr($2, length($2) - 4) == p { print $10 }' \
      /proc/net/tcp 2>/dev/null); do
    for fd in "/proc/$SINK_PID/fd"/*; do
      target="$(readlink "$fd" 2>/dev/null || true)"
      if [ "$target" = "socket:[$inode]" ]; then
        return 0
      fi
    done
  done
  return 1
}

ready=0
for _ in $(seq 1 50); do
  state=$(awk '{print $3}' "/proc/$SINK_PID/stat" 2>/dev/null)
  if [ -n "$state" ] && [ "$state" != "Z" ] && owns_port &&
     curl -sS --max-time 2 -o /dev/null -X POST -d "$SINK_PID" \
       "http://${COLLECT_HOST:-127.0.0.1}:$PORT/__ready" 2>/dev/null &&
     [ -f "$SINK_DIR/__ready" ]; then
    rm -f "$SINK_DIR/__ready"
    ready=1
    break
  fi
  sleep 0.1
done

if [ "$ready" -ne 1 ]; then
  echo "setup failed: integration stub did not listen on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
  tail -n 20 "$SINK_DIR/server.log" >&2 2>/dev/null || true
  exit 1
fi

exit 0
