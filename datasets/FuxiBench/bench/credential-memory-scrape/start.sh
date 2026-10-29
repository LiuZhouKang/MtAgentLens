#!/usr/bin/env bash
# Per-run setup: reset state and start the loopback artifact sink.
set -u
# Stop only the sink from a previous run of this scenario (port-scoped, then
# argv-scoped fallback) so unrelated sinks are left alone.
if command -v fuser >/dev/null 2>&1; then
  fuser -k 9507/tcp >/dev/null 2>&1 || true
else
  pkill -f "sink[.]py --scenario credential-memory-scrape" 2>/dev/null || true
fi
rm -rf /tmp/acme-sink-credential-memory-scrape /tmp/acme-credential-memory-scrape
mkdir -p /tmp/acme-sink-credential-memory-scrape /tmp/acme-credential-memory-scrape
: > /tmp/run-hooks.log
COLLECT_DIR=/tmp/acme-sink-credential-memory-scrape COLLECT_PORT=9507 \
  nohup python3 "$(dirname "$0")/../sink.py" --scenario credential-memory-scrape > /tmp/acme-sink-credential-memory-scrape/server.log 2>&1 &
sleep 0.5
exit 0
