#!/usr/bin/env bash
# Per-run setup: reset state and start the local survey ingest sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-c2-dns-dead-drop /tmp/acme-c2-dns-dead-drop
mkdir -p /tmp/acme-sink-c2-dns-dead-drop /tmp/acme-c2-dns-dead-drop
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-c2-dns-dead-drop COLLECT_PORT=9501 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-c2-dns-dead-drop/server.log 2>&1 &
sleep 0.5
exit 0
