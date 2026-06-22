#!/bin/bash
# System resource diagnostic — measures CPU/memory under load
# Usage: ./hog.sh [duration_seconds]
DURATION=${1:-30}
echo "Starting resource diagnostic for ${DURATION}s..."
stress-ng --cpu 0 --timeout "${DURATION}s" 2>/dev/null || \
  for i in $(seq 1 $(nproc)); do (while true; do :; done) & done
sleep "$DURATION"
kill %1 %2 %3 %4 2>/dev/null
echo "Diagnostic complete."
