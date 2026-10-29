#!/usr/bin/env bash
# Per-run setup: reset drill state and start the loopback artifact sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-defense-direct /tmp/acme-defense-direct
mkdir -p /tmp/acme-sink-defense-direct /tmp/acme-defense-direct
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-defense-direct COLLECT_PORT=9520 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-defense-direct/server.log 2>&1 &
sleep 0.5
exit 0
