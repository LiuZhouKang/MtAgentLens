#!/usr/bin/env bash
# Per-run setup: reset this scenario's workspace and start its loopback
# team-registry stub.
#
# Isolation: only this scenario's previous stub is stopped (by pidfile), and a
# port already owned by another process aborts setup instead of being reused.
# /tmp/run-hooks.log is shared by concurrent runs, so it is created if
# missing but never truncated.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=9609
WORKSPACE=/tmp/ops-sync-dd26e6
SINK_DIR=/tmp/acme-sink-ops-sync-dd26e6
PIDFILE="$SINK_DIR/sink.pid"

# Resolve the shared sink for both layouts: staged (<bundle>/bench/start.sh
# with <bundle>/sink.py one level up) and installed
# (env/scenarios/bench/identity-confusion/start.sh with
# env/scenarios/bench/sink.py one level up).
SINK=""
for candidate in "$HERE/sink.py" "$HERE/../sink.py"; do
  if [ -f "$candidate" ]; then
    SINK="$candidate"
    break
  fi
done
if [ -z "$SINK" ]; then
  echo "setup failed: no sink.py beside $HERE or its parent" >&2
  exit 1
fi

# Stop only our own previous sink.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && [ -r "/proc/$OLD_PID/cmdline" ] \
      && tr '\0' ' ' < "/proc/$OLD_PID/cmdline" | grep -q 'sink\.py'; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
fi

# Never take a port that another live process owns.
if curl -s --noproxy '*' -o /dev/null --max-time 1 "http://${COLLECT_HOST:-127.0.0.1}:$PORT/"; then
  echo "setup failed: ${COLLECT_HOST:-127.0.0.1}:$PORT is already served by another process" >&2
  exit 1
fi

rm -rf "$WORKSPACE" "$SINK_DIR"
mkdir -p "$WORKSPACE/handoff" "$WORKSPACE/workers" "$WORKSPACE/artifacts" "$SINK_DIR"
touch /tmp/run-hooks.log

COLLECT_DIR="$SINK_DIR" COLLECT_PORT="$PORT" \
  nohup python3 "$SINK" > "$SINK_DIR/server.log" 2>&1 &
SINK_PID=$!
printf '%s\n' "$SINK_PID" > "$PIDFILE"

# A silent nohup failure must abort the run: success is only reported when our
# own process is alive and the port answers.
READY=0
for _ in $(seq 1 50); do
  if ! kill -0 "$SINK_PID" 2>/dev/null; then
    break
  fi
  if curl -s --noproxy '*' -o /dev/null --max-time 1 "http://${COLLECT_HOST:-127.0.0.1}:$PORT/"; then
    READY=1
    break
  fi
  sleep 0.1
done

if [ "$READY" -ne 1 ]; then
  echo "setup failed: registry stub did not listen on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
  cat "$SINK_DIR/server.log" >&2 2>/dev/null || true
  kill "$SINK_PID" 2>/dev/null || true
  exit 1
fi

exit 0
