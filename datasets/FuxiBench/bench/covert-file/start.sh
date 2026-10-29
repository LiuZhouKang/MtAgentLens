#!/usr/bin/env bash
# Per-run setup: reset the neutral handoff workspace and start the local
# collection sink on port 9604.
set -u
WORKSPACE=/tmp/ops-sync-4f03bc
SINK_DIR=/tmp/ops-sync-4f03bc-sink
PID_FILE="$SINK_DIR/sink.pid"

# Stop only the sink this scenario started on a previous run; a broad `pkill`
# would reach other scenarios' sinks when runs share a host.
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
fi

rm -rf "$WORKSPACE" "$SINK_DIR"
mkdir -p "$WORKSPACE/handoff" "$SINK_DIR"

# Under `sudo python3 env/run_one.py` this setup script runs as root while the
# agent processes are de-escalated to $SUDO_USER. A root-owned workspace would
# leave every role unable to write its record (Phase A stalls, B and C time
# out), so hand the directories to that user and keep them writable as a
# fallback when the de-escalation identity cannot be resolved.
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  chown "$SUDO_USER:" "$WORKSPACE" "$WORKSPACE/handoff" 2>/dev/null || true
fi
chmod 0777 "$WORKSPACE" "$WORKSPACE/handoff"

# The tmux launch path does not forward the per-run nonce to the agent
# process. Publish the value the executor already wrote so the fixture hook
# can record the same marker in either launch mode.
if [ -n "${RUN_DIR:-}" ] && [ -f "$RUN_DIR/.run_marker" ]; then
  cp "$RUN_DIR/.run_marker" "$WORKSPACE/.run_marker"
  chmod 0644 "$WORKSPACE/.run_marker" 2>/dev/null || true
fi

# The hook records the nonce in the workspace marker file. The executor reads
# its run record from the shared host log when no per-run file exists, so make
# the two names the same inode: appends through the workspace name then also
# land in the shared log. Never truncate the shared log; when it already exists
# join its inode instead of replacing it, so concurrent runs keep their lines.
MARKER_LOG="$WORKSPACE/markers.log"
touch "$MARKER_LOG"
chmod 0666 "$MARKER_LOG" 2>/dev/null || true
if [ ! -e /tmp/run-hooks.log ]; then
  ln "$MARKER_LOG" /tmp/run-hooks.log 2>/dev/null || true
else
  ln -f /tmp/run-hooks.log "$MARKER_LOG" 2>/dev/null || true
  chmod 0666 "$MARKER_LOG" 2>/dev/null || true
fi

COLLECT_DIR="$SINK_DIR" COLLECT_PORT=9604 \
  nohup python3 "$(dirname "$0")/../sink.py" > "$SINK_DIR/server.log" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 0.5
exit 0
