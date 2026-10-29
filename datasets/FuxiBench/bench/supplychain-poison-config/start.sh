#!/usr/bin/env bash
# Per-run setup: reset state and start the artifact sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-supplychain-poison-config /tmp/acme-supplychain-poison-config
mkdir -p /tmp/acme-sink-supplychain-poison-config /tmp/acme-supplychain-poison-config
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-supplychain-poison-config COLLECT_PORT=9542 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-supplychain-poison-config/server.log 2>&1 &
sleep 0.5
exit 0
