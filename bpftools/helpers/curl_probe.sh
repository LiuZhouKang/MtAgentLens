#!/bin/bash
OUTPUT_FILE=/tmp/probe_results.txt
ENDPOINTS_FILE=/home/lzk/mtagentlens/env/runs/val-roundA-network-anomaly/fixtures/network-anomaly/endpoints.txt

echo "" > "$OUTPUT_FILE"

while IFS= read -r line; do
  x=$line
  x="${x#"${x%%[![:space:]]*}"}"
  [ -z "$x" ] && continue
  case "$x" in #*) continue ;; esac

  echo "=== $x ===" >> "$OUTPUT_FILE"

  for i in 1 2 3; do
    result=$(/usr/bin/curl -v --max-time 5 -o /dev/null \
      --write-out "http_code=%{http_code}|dns_sec=%{time_namelookup}|tcp_sec=%{time_connect}|total_sec=%{time_total}" \
      "$x" 2>&1)

    http_code=$(/usr/bin/grep -o "http_code=[^|]*" <<< "$result" | /usr/bin/cut -d= -f2)
    dns_sec=$(/usr/bin/grep -o "dns_sec=[^|]*" <<< "$result" | /usr/bin/cut -d= -f2)
    tcp_sec=$(/usr/bin/grep -o "tcp_sec=[^|]*" <<< "$result" | /usr/bin/cut -d= -f2)
    total_sec=$(/usr/bin/grep -o "total_sec=[^|]*" <<< "$result" | /usr/bin/cut -d= -f2)

    error_msg="ok"
    if /usr/bin/grep -qE "(refused|timed out|could not resolve|failed to connect|not known)" <<< "$result" 2>/dev/null; then
      error_msg="connection_error"
    fi

    echo "  run=$i http=${http_code:-N/A} dns=${dns_sec:-N/A}s tcp=${tcp_sec:-N/A}s total=${total_sec:-N/A}s error=$error_msg" >> "$OUTPUT_FILE"
  done
  echo "" >> "$OUTPUT_FILE"
done < "$ENDPOINTS_FILE"
echo "DONE"
