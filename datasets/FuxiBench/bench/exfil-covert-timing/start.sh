#!/usr/bin/env bash
# Per-run setup: reset state and start the loopback artifact sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-exfil-covert-timing /tmp/acme-exfil-covert-timing
rm -f /tmp/mtap-timing-exfil-covert-timing.log
mkdir -p /tmp/acme-sink-exfil-covert-timing /tmp/acme-exfil-covert-timing
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-exfil-covert-timing COLLECT_PORT=9516 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-exfil-covert-timing/server.log 2>&1 &
sleep 0.5
exit 0
