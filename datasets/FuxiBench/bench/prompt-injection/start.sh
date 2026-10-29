#!/usr/bin/env bash
# Per-run setup: reset state and start the local artifact sink.
set -u
pkill -f "bench/.*sink\.py" 2>/dev/null || true
rm -rf /tmp/acme-sink-prompt-injection /tmp/acme-prompt-injection
mkdir -p /tmp/acme-sink-prompt-injection /tmp/acme-prompt-injection
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-prompt-injection COLLECT_PORT=9528 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-prompt-injection/server.log 2>&1 &
sleep 0.5
exit 0
