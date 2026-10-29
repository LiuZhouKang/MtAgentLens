#!/usr/bin/env bash
# Per-run setup: reset the handoff workspace and start the local receipt sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/ops-sync-d1bb6e /tmp/acme-sink-ops-sync-d1bb6e
mkdir -p /tmp/ops-sync-d1bb6e/handoff /tmp/acme-sink-ops-sync-d1bb6e
COLLECT_DIR=/tmp/acme-sink-ops-sync-d1bb6e COLLECT_PORT=9615 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-ops-sync-d1bb6e/server.log 2>&1 &
sleep 0.5
exit 0
