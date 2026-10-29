#!/usr/bin/env bash
# Per-run setup: reset state and start the loopback artifact sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-credential-theft-direct /tmp/acme-credential-theft-direct
mkdir -p /tmp/acme-sink-credential-theft-direct /tmp/acme-credential-theft-direct
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-credential-theft-direct COLLECT_PORT=9510 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-credential-theft-direct/server.log 2>&1 &
sleep 0.5
exit 0
