#!/bin/bash
# 内存占用 v2 — 高精度 smaps_rollup 测量
# 用法: sudo bash compare/scripts/perf/memory_measure.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
COLLECTOR="$PROJECT/collector/build/release/mtagentprism"
OUT="$PROJECT/compare/results/perf/memory_footprint_v2.json"
DUR=180
RUNS=3

echo "=== 内存测量 v2 (smaps_rollup) ==="
echo "  $RUNS runs × ${DUR}s each"
echo "  Collector: $COLLECTOR"

declare -a ALL_RSS=()
declare -a RUN_MEANS=()
declare -a RUN_GROWTHS=()
RUN_DATA=""

for run in $(seq 1 $RUNS); do
    echo ""
    echo "── Run $run/$RUNS ──"

    # Start collector in background (as root, no inner sudo)
    $COLLECTOR trace --ssl=true --process=true --quiet &
    CPID=$!
    echo "  Collector PID: $CPID"
    sleep 5

    # Check collector is alive
    if ! kill -0 $CPID 2>/dev/null; then
        echo "  ERROR: collector died immediately"
        wait $CPID 2>/dev/null
        continue
    fi

    # Sample RSS via smaps_rollup for DUR seconds
    RSS_VALS=()
    DEADLINE=$(awk "BEGIN {print $(date +%s.%N) + $DUR}")
    while [ "$(date +%s.%N)" \< "$DEADLINE" ]; do
        RSS_KB=$(awk '/^Rss:/ {print $2}' /proc/$CPID/smaps_rollup 2>/dev/null || echo 0)
        if [ "$RSS_KB" != "0" ] && [ -n "$RSS_KB" ]; then
            RSS_MB=$(awk "BEGIN {printf \"%.3f\", $RSS_KB/1024}")
            RSS_VALS+=("$RSS_MB")
        fi
        sleep 1
    done

    # Stop collector
    kill $CPID 2>/dev/null
    wait $CPID 2>/dev/null
    echo "  Collector stopped"

    N=${#RSS_VALS[@]}
    if [ "$N" -lt 10 ]; then
        echo "  ⚠ Too few samples ($N), skipping"
        continue
    fi

    # Compute stats with awk
    MEAN=$(printf '%s\n' "${RSS_VALS[@]}" | awk '{s+=$1; n++} END {printf "%.3f", s/n}')
    SORTED=$(printf '%s\n' "${RSS_VALS[@]}" | sort -n)
    MIN=$(echo "$SORTED" | head -1)
    MAX=$(echo "$SORTED" | tail -1)
    MEDIAN=$(echo "$SORTED" | awk -v n=$N 'NR==int(n/2){print $1}')
    P95=$(echo "$SORTED" | awk -v n=$N 'NR==int(n*0.95){print $1}')
    STD=$(printf '%s\n' "${RSS_VALS[@]}" | awk -v m=$MEAN '{d=$1-m; s+=d*d; n++} END {printf "%.3f", sqrt(s/n)}')

    # Growth rate via linear regression
    GROWTH=$(printf '%s\n' "${RSS_VALS[@]}" | awk -v n=$N '
      { y[NR]=$1; x[NR]=NR; sx+=NR; sy+=$1 }
      END {
        xm=sx/n; ym=sy/n; num=0; den=0
        for(i=1;i<=n;i++) { num+=(x[i]-xm)*(y[i]-ym); den+=(x[i]-xm)^2 }
        slope=(den>0 ? num/den : 0)
        printf "%.4f", slope*60
      }')

    echo "  RSS: ${MEAN} ± ${STD} MB (n=$N, min=$MIN, max=$MAX, p95=$P95)"
    echo "  Growth: $GROWTH MB/min"

    RUN_MEANS+=("$MEAN")
    RUN_GROWTHS+=("$GROWTH")
    ALL_RSS+=("${RSS_VALS[@]}")

    # Build run JSON
    RAW_JSON=$(printf '%s,' "${RSS_VALS[@]}" | sed 's/,$//')
    RUN_DATA+="{\"run\":$run,\"n\":$N,\"mean\":$MEAN,\"std\":$STD,\"min\":$MIN,\"max\":$MAX,\"median\":$MEDIAN,\"p95\":$P95,\"growth\":$GROWTH,\"raw\":[$RAW_JSON]},"
done

# ── Summary ──
if [ ${#ALL_RSS[@]} -eq 0 ]; then
    echo "ERROR: No data collected"
    exit 1
fi

ALL_MEAN=$(printf '%s\n' "${ALL_RSS[@]}" | awk '{s+=$1; n++} END {printf "%.3f", s/n}')
ALL_N=$(echo "${ALL_RSS[@]}" | wc -w)
ALL_SORTED=$(printf '%s\n' "${ALL_RSS[@]}" | sort -n)
ALL_MIN=$(echo "$ALL_SORTED" | head -1)
ALL_MAX=$(echo "$ALL_SORTED" | tail -1)
ALL_MEDIAN=$(echo "$ALL_SORTED" | awk -v n=$ALL_N 'NR==int(n/2){print $1}')
ALL_P95=$(echo "$ALL_SORTED" | awk -v n=$ALL_N 'NR==int(n*0.95){print $1}')
ALL_STD=$(printf '%s\n' "${ALL_RSS[@]}" | awk -v m=$ALL_MEAN '{d=$1-m; s+=d*d; n++} END {printf "%.3f", sqrt(s/n)}')

CROSS_MEAN=$(printf '%s\n' "${RUN_MEANS[@]}" | awk '{s+=$1; n++} END {printf "%.3f", s/n}')
CROSS_STD=$(printf '%s\n' "${RUN_MEANS[@]}" | awk -v m=$CROSS_MEAN '{d=$1-m; s+=d*d; n++} END {printf "%.4f", sqrt(s/n)}')

GROWTH_MEAN=$(printf '%s\n' "${RUN_GROWTHS[@]}" | awk '{s+=$1; n++} END {printf "%.4f", s/n}')

NRUNS=${#RUN_MEANS[@]}
RUN_DATA_CLEAN=$(echo "$RUN_DATA" | sed 's/,$//')

cat > "$OUT" << JSONEOF
{
  "experiment": "4.4_v2",
  "method": "smaps_rollup byte-precision RSS sampling via /proc/PID/smaps_rollup",
  "design": "$NRUNS runs × ${DUR}s each, collector trace --ssl --process --quiet",
  "memory": {
    "duration_s": $DUR,
    "runs": $NRUNS,
    "total_samples": $ALL_N,
    "rss": {
      "n": $ALL_N,
      "mean": $ALL_MEAN,
      "stddev": $ALL_STD,
      "min": $ALL_MIN,
      "max": $ALL_MAX,
      "median": $ALL_MEDIAN,
      "p95": $ALL_P95
    },
    "across_runs_mean": $CROSS_MEAN,
    "across_runs_std": $CROSS_STD,
    "peak_rss_mb": $ALL_MAX,
    "steady_state_rss_mb": $CROSS_MEAN,
    "growth_rate_mb_per_min": $GROWTH_MEAN
  },
  "individual_runs": [$RUN_DATA_CLEAN]
}
JSONEOF

echo ""
echo "=== 摘要 ==="
echo "  RSS: ${ALL_MEAN} ± ${ALL_STD} MB"
echo "  跨run变异: ${CROSS_STD} MB"
echo "  增长率: ${GROWTH_MEAN} MB/min"
echo "  ✓ 保存: $OUT"
