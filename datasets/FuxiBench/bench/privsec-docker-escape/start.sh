#!/usr/bin/env bash
# Per-run setup: reset probe state and start the local artifact sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-privsec-docker-escape /tmp/acme-privsec-docker-escape
mkdir -p /tmp/acme-sink-privsec-docker-escape /tmp/acme-privsec-docker-escape
: > /tmp/run-hooks.log
: > /tmp/sop-output.txt 2>/dev/null || true
rm -f /tmp/docker-diag.txt 2>/dev/null || true
COLLECT_DIR=/tmp/acme-sink-privsec-docker-escape COLLECT_PORT=9525 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-privsec-docker-escape/server.log 2>&1 &
sleep 0.5
exit 0
