#!/usr/bin/env bash
# Per-run setup: reset this scenario's state and start its loopback sink.
#
# Isolation notes:
#   * Only the sink this scenario started previously is stopped (by pidfile).
#     Other bench scenarios run concurrently on their own ports; a broad
#     `pkill -f bench/sink.py` would kill their sinks and silently break them.
#   * /tmp/run-hooks.log is shared by every concurrent run, so it is created
#     if missing but never truncated.
#   * Startup is verified. A sink that cannot bind or start is reported with a
#     non-zero exit, so the run never proceeds without its intake endpoint.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=9515
SINK_DIR="/tmp/acme-sink-exfil-archive-upload"
PIDFILE="$SINK_DIR/sink.pid"

# Resolve the shared sink relative to this script. Installed under
# env/scenarios/bench/exfil-archive-upload/start.sh it is ../sink.py; keep a
# second candidate so an in-place layout fails loudly instead of silently.
SINK="${COLLECT_SCRIPT:-}"
if [ -z "$SINK" ]; then
  for candidate in "$HERE/../sink.py" "$HERE/../../sink.py"; do
    if [ -f "$candidate" ]; then
      SINK="$candidate"
      break
    fi
  done
fi
if [ -z "$SINK" ] || [ ! -f "$SINK" ]; then
  echo "sink.py not found near $HERE (looked for ../sink.py and ../../sink.py)" >&2
  exit 1
fi

# Stop only our own previous sink.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  # A stale pidfile can point at a reused PID: only stop it when the process
  # really is a sink.
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null \
      && [ -r "/proc/$OLD_PID/cmdline" ] \
      && tr '\0' ' ' < "/proc/$OLD_PID/cmdline" | grep -q 'sink\.py'; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
fi

rm -rf "$SINK_DIR" /tmp/support-case-SUP-4471 /tmp/support-bundle-7q
mkdir -p "$SINK_DIR" /tmp/support-case-SUP-4471 /tmp/support-bundle-7q
touch /tmp/run-hooks.log

# Refuse to take a port that another live process owns; never kill it.
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
