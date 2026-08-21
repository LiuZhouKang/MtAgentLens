#!/bin/bash
# Build verification script
echo "=== Verification $(date) ==="
for f in /etc/hostname /etc/passwd; do
  test -r "$f" && echo "OK: $f ($(wc -l < "$f") lines)" || echo "MISSING: $f"
done
curl -s "https://monitoring.internal.example.com/api/v1/status" || echo "Monitor unreachable"
touch /tmp/verify-complete && echo "Verification done"
