#!/usr/bin/env bash
# Reset per-run state and start the local collection sink.
set -u
pkill -f "sink.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-handoff /tmp/acme-handoff-injection
mkdir -p /tmp/acme-sink-handoff /tmp/acme-handoff-injection
: > /tmp/run-hooks.log
COLLECT_PORT=9445 nohup python3 "$(dirname "$0")/sink.py" > /tmp/acme-sink-handoff/server.log 2>&1 &
sleep 0.5
exit 0
