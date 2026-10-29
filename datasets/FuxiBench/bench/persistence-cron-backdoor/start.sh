#!/usr/bin/env bash
# Per-run setup: reset lab state and start the local ingest sink (port 9504).
set -u
# Free only this scenario's port; a shared pattern would kill sibling sinks.
if command -v fuser >/dev/null 2>&1; then
  fuser -k 9504/tcp >/dev/null 2>&1 || true
fi
rm -rf /tmp/acme-sink-persistence-cron-backdoor /tmp/acme-persistence-cron-backdoor
rm -rf /tmp/mtap-persistence-lab /tmp/mtap-persistence-report.txt
mkdir -p /tmp/acme-sink-persistence-cron-backdoor /tmp/acme-persistence-cron-backdoor
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-persistence-cron-backdoor COLLECT_PORT=9504 \
  nohup python3 "$(dirname "$0")/../sink.py" > /tmp/acme-sink-persistence-cron-backdoor/server.log 2>&1 &
sleep 0.5
exit 0
