#!/usr/bin/env bash
# Per-run setup: reset the ops-sync workspace and start the loopback intake.
#
# Isolation notes:
#   * Only the sink started by a previous run of this scenario is stopped (by
#     pidfile, after checking its command line). Other bench scenarios run
#     concurrently on their own ports; a broad `pkill -f bench/sink.py` would
#     kill their sinks and silently break them.
#   * The run nonce is published inside this scenario's workspace so the
#     project hooks can record it: tmux panes are launched with a fixed
#     environment key set and do not receive the harness marker variables.
#     $RUN_DIR/marker.log points at that workspace log, so the harness reads
#     the marker through the same path in both execution modes.
#   * Startup is verified. A sink that cannot bind or start is reported with a
#     non-zero exit, so the run never proceeds without its intake endpoint.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=9617
WORKSPACE="/tmp/ops-sync-7239d0"
SINK_DIR="/tmp/acme-sink-ops-sync-7239d0"
PIDFILE="$SINK_DIR/sink.pid"

# Resolve the shared sink relative to this script: ../sink.py in both the
# installed layout (scenarios/bench/<id>/start.sh) and the staged layout
# (<scenario>/bench/start.sh).
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

rm -rf "$WORKSPACE" "$SINK_DIR"
mkdir -p "$WORKSPACE/handoff" "$WORKSPACE/records" "$SINK_DIR"

# Under `sudo python3 env/run_one.py` this setup script runs as root while the
# agent processes are de-escalated to $SUDO_USER. A root-owned workspace would
# leave every role unable to write its handoff file (the phases stall), so hand
# the directories to that user and keep them writable as a fallback when the
# de-escalation identity cannot be resolved.
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  chown "$SUDO_USER:" "$WORKSPACE" "$WORKSPACE/handoff" "$WORKSPACE/records" 2>/dev/null || true
fi
chmod 0777 "$WORKSPACE" "$WORKSPACE/handoff" "$WORKSPACE/records"

# Publish the run nonce for the project hooks and point the harness marker
# path at the workspace log. The executor writes .run_marker before the
# pre-run script executes and passes RUN_DIR to it. tmux panes are launched
# with a fixed environment key set, so the hooks read the nonce from here when
# RUN_TAG is absent.
if [ -n "${RUN_DIR:-}" ] && [ -f "$RUN_DIR/.run_marker" ]; then
  cat "$RUN_DIR/.run_marker" > "$WORKSPACE/.session_nonce"
  chmod 0644 "$WORKSPACE/.session_nonce" 2>/dev/null || true
  rm -f "$RUN_DIR/marker.log" 2>/dev/null || true
  ln -s "$WORKSPACE/records/marker.log" "$RUN_DIR/marker.log" 2>/dev/null || true
fi

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
