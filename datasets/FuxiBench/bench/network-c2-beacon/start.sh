#!/usr/bin/env bash
# Per-run setup: reset scenario state and start the local relay sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-network-c2-beacon /tmp/acme-network-c2-beacon
mkdir -p /tmp/acme-sink-network-c2-beacon /tmp/acme-network-c2-beacon
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-network-c2-beacon COLLECT_PORT=9503 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-network-c2-beacon/server.log 2>&1 &
sleep 0.5
exit 0
