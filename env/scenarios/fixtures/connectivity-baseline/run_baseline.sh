#!/usr/bin/env bash
# Connectivity baseline — run 3x curl per endpoint, record timing metrics.
set -euo pipefail

ENDPOINTS_FILE="$(dirname "$0")/endpoints.txt"
OUTPUT_FILE="$(dirname "$0")/baseline_results.tsv"

# curl write-out format: tab-separated fields
FMT='%{http_code}\t%{time_namelookup}\t%{time_connect}\t%{time_starttransfer}\t%{time_total}\t%{remote_ip}\t%{errormsg}'

echo -e "url\trun\thttp_code\tdns_s\ttcp_connect_s\tttfb_s\ttotal_s\tremote_ip\terror" > "$OUTPUT_FILE"

while IFS= read -r line; do
  # Skip empty lines and comments
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  url="$line"

  for run in 1 2 3; do
    printf "→ %s (run %d)... " "$url" "$run"
    raw=$(curl -sS -w "$FMT" --max-time 5 -o /dev/null "$url" 2>&1) || true
    # raw is now: http_code\tdns\ttcp\tttfb\ttotal\tremote_ip\terrormsg
    printf "%s\n" "$raw"
    echo -e "${url}\t${run}\t${raw}" >> "$OUTPUT_FILE"
  done
done < "$ENDPOINTS_FILE"

echo ""
echo "=== Results written to $OUTPUT_FILE ==="
