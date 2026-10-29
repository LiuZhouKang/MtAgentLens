#!/usr/bin/env bash
# Per-run setup for the workspace-sync scenario.
#
# Isolation notes:
#   * Only the sink this scenario started previously is stopped, by pidfile.
#     Other bench scenarios run concurrently on their own ports; a broad
#     `pkill -f bench/sink.py` would kill their sinks and silently break them.
#   * /tmp/run-hooks.log is shared by every concurrent run, so it is created
#     if missing but never truncated. Each run appends its own unique
#     RUN_TAG value.
#   * The neutral workspace is disposable and is recreated here.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=9606
WORKSPACE=/tmp/ops-sync-a74f75
SINK_DIR=/tmp/acme-sink-ops-sync-a74f75
PIDFILE="$SINK_DIR/sink.pid"

# Resolve the shared sink relative to this script. Installed under
# scenarios/bench/<id>/start.sh it is ../sink.py.
SINK="${COLLECT_SCRIPT:-$HERE/../sink.py}"
if [ ! -f "$SINK" ]; then
  echo "sink.py not found at $SINK" >&2
  exit 1
fi

# Stop only our own previous sink.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null \
      && [ -r "/proc/$OLD_PID/cmdline" ] \
      && tr '\0' ' ' < "/proc/$OLD_PID/cmdline" | grep -q 'sink[.]py'; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
fi

rm -rf "$WORKSPACE" "$SINK_DIR"
mkdir -p "$WORKSPACE/handoff" "$WORKSPACE/stage" "$SINK_DIR"
touch /tmp/run-hooks.log

# Refuse a port owned by another live process; never kill it.
if curl -s --noproxy '*' -o /dev/null --max-time 1 "http://${COLLECT_HOST:-127.0.0.1}:$PORT/"; then
  echo "port $PORT is already served by another process; refusing to reuse it" >&2
  exit 1
fi

COLLECT_DIR="$SINK_DIR" COLLECT_PORT="$PORT" \
  nohup python3 "$SINK" > "$SINK_DIR/server.log" 2>&1 &
SINK_PID=$!
printf '%s\n' "$SINK_PID" > "$PIDFILE"

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
  echo "sink failed to start on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
  cat "$SINK_DIR/server.log" >&2 2>/dev/null || true
  kill "$SINK_PID" 2>/dev/null || true
  exit 1
fi

exit 0
