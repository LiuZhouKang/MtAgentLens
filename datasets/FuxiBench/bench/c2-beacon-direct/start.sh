#!/usr/bin/env bash
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-c2-beacon-direct /tmp/acme-c2-beacon-direct
mkdir -p /tmp/acme-sink-c2-beacon-direct /tmp/acme-c2-beacon-direct
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-c2-beacon-direct COLLECT_PORT=9500 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-c2-beacon-direct/server.log 2>&1 &
sleep 0.5
exit 0
