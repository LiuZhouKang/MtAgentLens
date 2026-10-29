#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Measure collector overhead on a fixed, real workload (P0.5 deliverable 7)
# =============================================================================
# Runs `mtagentlens record` against one `claude -p` turn and samples the
# collector's CPU time and resident memory once per second. The resulting JSON
# is the committed baseline; scripts/wire/check_baselines.py asserts it is
# complete and collector/tests/overhead_gate.rs enforces the offline part.
#
# This needs sudo (eBPF) and a live LLM endpoint, so it is NOT part of
# scripts/verify.sh. Run it deliberately when the collector changes.
#
# Usage:
#   ./scripts/wire/measure-overhead.sh [seconds] [out.json]
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DURATION="${1:-45}"
OUT="${2:-$ROOT/docs/evaluation/overhead-baseline.json}"
SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-}"
BIN="$ROOT/collector/build/release/mtagentlens"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/debug/mtagentlens"
CLAUDE_ELF="$(readlink -f "$(command -v claude)")"
LITE="${MTL_LITE:-0}"
WORKLOAD_RUNS="${MTL_WORKLOAD_RUNS:-1}"
IDLE_SECONDS="${MTL_IDLE_SECONDS:-0}"
PROFILE_LABEL="full"; [ "$LITE" = "1" ] && PROFILE_LABEL="observer-off"
WORK_DIR="$(mktemp -d /tmp/mtl-overhead.XXXXXX)"
LOG="$WORK_DIR/record.log"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "collector binary not found (run: cd collector && cargo build)"
[ -n "$CLAUDE_ELF" ] || die "claude binary not found"

mkdir -p "$(dirname "$OUT")"
echo "collector: $BIN"
echo "workload:  one claude -p turn"
echo "duration:  ${DURATION}s"
echo "workdir:   $WORK_DIR"

COLLECTOR_PID=""
cleanup() {
  if [ -n "$COLLECTOR_PID" ]; then
    printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$COLLECTOR_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Start the collector. `record` always enables the web server; use a port that
# is unlikely to collide with the dev server (7395).
RECORD_ARGS=(record -c claude --binary-path "$CLAUDE_ELF" -o "$LOG" --server-port 7396)
[ "$LITE" = "1" ] && RECORD_ARGS+=(--disable-responsibility-observer)
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' "$BIN" "${RECORD_ARGS[@]}" \
  >"$WORK_DIR/collector.out" 2>&1 &
SUDO_PID=$!

# sudo may fork; find the real collector process. Do NOT use `pgrep -P | head -1`:
# the collector spawns the eBPF programs, and the first child is one of those.
# Do NOT readlink /proc/PID/exe either: the
# collector runs as root and a non-root readlink returns EACCES. cmdline is
# world-readable, so match its first argument instead.
COLLECTOR_PID=""
for _ in $(seq 1 50); do
  for candidate in $(pgrep -P "$SUDO_PID" 2>/dev/null) "$SUDO_PID"; do
    argv0="$(tr '\0' '\n' < "/proc/$candidate/cmdline" 2>/dev/null | head -1)"
    if [ "$argv0" = "$BIN" ]; then
      COLLECTOR_PID="$candidate"
      break
    fi
  done
  [ -n "$COLLECTOR_PID" ] && break
  sleep 0.2
done
[ -n "$COLLECTOR_PID" ] || die "collector process did not appear; see $WORK_DIR/collector.out"
echo "collector pid: $COLLECTOR_PID"

CLK_TCK="$(getconf CLK_TCK)"

# The collector's cost is the whole tree: the Rust process, the eBPF programs it
# spawns, and the inference server. Reaped children are accounted in the
# parent's cutime/cstime, so summing utime+stime+cutime+cstime over the live
# tree is consistent across samples.
descendants() { # descendants <pid> — pid plus every live descendant
  local queue=("$1")
  while [ "${#queue[@]}" -gt 0 ]; do
    local pid="${queue[0]}"
    queue=("${queue[@]:1}")
    printf '%s\n' "$pid"
    for child in $(pgrep -P "$pid" 2>/dev/null); do
      queue+=("$child")
    done
  done
}

read_cpu_ticks() { # read_cpu_ticks <pid>
  awk '{print $14 + $15 + $16 + $17}' "/proc/$1/stat" 2>/dev/null || echo 0
}
read_rss_kib() { # read_rss_kib <pid>
  awk '/^VmRSS:/ {print $2}' "/proc/$1/status" 2>/dev/null || echo 0
}
tree_cpu_ticks() {
  local total=0
  for pid in $(descendants "$COLLECTOR_PID"); do
    total=$((total + $(read_cpu_ticks "$pid")))
  done
  printf '%s\n' "$total"
}
tree_rss_kib() {
  local total=0
  for pid in $(descendants "$COLLECTOR_PID"); do
    total=$((total + $(read_rss_kib "$pid")))
  done
  printf '%s\n' "$total"
}

sleep 2
START_TICKS="$(tree_cpu_ticks)"
START_EPOCH="$(date +%s)"
PEAK_RSS=0
PEAK_PROCESSES=0
SAMPLES=0
RSS_SAMPLES="$WORK_DIR/rss-samples.txt"
: > "$RSS_SAMPLES"

# Fixed workload, started after the collector is attached. Idle runs skip it.
WORKLOAD_PID=""
if [ "$IDLE_SECONDS" = "0" ]; then
  (
    for run in $(seq 1 "$WORKLOAD_RUNS"); do
      cd /tmp && timeout 60 claude -p "Reply with exactly: PONG. Do not use tools." \
        >>"$WORK_DIR/claude.out" 2>&1
    done
  ) &
  WORKLOAD_PID=$!
fi

while [ $(( $(date +%s) - START_EPOCH )) -lt "$DURATION" ]; do
  RSS="$(tree_rss_kib)"
  [ "${RSS:-0}" -gt "$PEAK_RSS" ] && PEAK_RSS="$RSS"
  LIVE="$(descendants "$COLLECTOR_PID" | wc -l)"
  [ "${LIVE:-0}" -gt "$PEAK_PROCESSES" ] && PEAK_PROCESSES="$LIVE"
  for pid in $(descendants "$COLLECTOR_PID"); do
    COMM="$(cat "/proc/$pid/comm" 2>/dev/null || echo unknown)"
    printf '%s %s\n' "$COMM" "$(read_rss_kib "$pid")" >>"$RSS_SAMPLES"
  done
  SAMPLES=$((SAMPLES + 1))
  sleep 1
done

END_TICKS="$(tree_cpu_ticks)"
END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))
[ "$ELAPSED" -gt 0 ] || ELAPSED=1

[ -n "$WORKLOAD_PID" ] && wait "$WORKLOAD_PID" 2>/dev/null || true

# Graceful SIGINT so phase 2 finalizes and the log is complete.
sudo -n kill -INT "$COLLECTOR_PID" 2>/dev/null || \
  printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$COLLECTOR_PID" 2>/dev/null || true
for _ in $(seq 1 40); do
  kill -0 "$SUDO_PID" 2>/dev/null || break
  sleep 0.5
done
COLLECTOR_PID=""  # already stopped; the EXIT trap must not signal a reused pid

EVENTS="$(grep -c '^{' "$LOG" 2>/dev/null || echo 0)"
LOG_BYTES="$(stat -c %s "$LOG" 2>/dev/null || echo 0)"
CPU_TICKS=$((END_TICKS - START_TICKS))

python3 - "$OUT" "$CPU_TICKS" "$CLK_TCK" "$ELAPSED" "$PEAK_RSS" "$EVENTS" \
  "$LOG_BYTES" "$BIN" "$DURATION" "$WORK_DIR" "$PEAK_PROCESSES" "$RSS_SAMPLES" \
  "$PROFILE_LABEL" "$WORKLOAD_RUNS" "$IDLE_SECONDS" <<'PY'
import hashlib, json, os, platform, subprocess, sys

(out, cpu_ticks, clk_tck, elapsed, peak_rss_kib, events, log_bytes,
 binary, duration, work_dir, peak_processes, rss_samples,
 profile, workload_runs, idle_seconds) = sys.argv[1:]

cpu_ticks = int(cpu_ticks); clk_tck = int(clk_tck); elapsed = int(elapsed)
peak_rss_kib = int(peak_rss_kib); events = int(events); log_bytes = int(log_bytes)
duration = int(duration); peak_processes = int(peak_processes)

peak_by_process: dict[str, int] = {}
try:
    with open(rss_samples) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) != 2:
                continue
            name, kib = parts[0], int(parts[1])
            peak_by_process[name] = max(peak_by_process.get(name, 0), kib)
except OSError:
    pass
peak_by_process_mib = {
    name: round(kib / 1024, 1)
    for name, kib in sorted(peak_by_process.items(), key=lambda item: -item[1])
}

def one_line(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""

baseline = {
    "schema": "mtagentlens.overhead-baseline/1",
    "measured_at": __import__("datetime").datetime.now().astimezone().isoformat(),
    "workload": {
        "profile": profile,
        "description": (
            "idle (no agent workload)" if int(idle_seconds) > 0
            else f"{workload_runs} x claude -p turn (Reply with exactly: PONG. Do not use tools.)"
        ),
        "workload_runs": int(workload_runs),
        "idle": int(idle_seconds) > 0,
        "duration_seconds_target": duration,
        "duration_seconds_measured": elapsed,
    },
    "collector": {
        "binary": binary,
        "binary_sha256": hashlib.sha256(open(binary, "rb").read()).hexdigest(),
        "command": "record -c claude --binary-path <claude ELF> --server-port 7396",
        "cpu_seconds": round(cpu_ticks / clk_tck, 3),
        "cpu_percent_mean": round((cpu_ticks / clk_tck) / elapsed * 100, 2),
        "rss_peak_mib": round(peak_rss_kib / 1024, 1),
        "rss_peak_by_process_mib": peak_by_process_mib,
        "peak_processes": peak_processes,
        "events": events,
        "log_bytes": log_bytes,
        "events_per_second": round(events / elapsed, 2),
        "log_bytes_per_second": round(log_bytes / elapsed, 1),
    },
    "environment": {
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "claude_version": one_line(["claude", "--version"]),
        "node_version": one_line(["node", "--version"]),
    },
    "raw_workdir": work_dir,
    "caveat": ("CPU and RSS cover the whole collector process tree (the Rust "
               "binary and the eBPF programs it spawns); reaped children are "
               "counted through "
               "cutime/cstime. Samples are once per second, so a very short-lived "
               "child can be missed. Event counts are lines beginning with '{' in "
               "the collector log. This baseline is a budget input, not a "
               "benchmark: the offline gate leaves a wide margin for CI variance."),
}
with open(out, "w") as handle:
    json.dump(baseline, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
print(json.dumps(baseline["collector"], indent=2))
PY

echo "wrote $OUT"
echo "raw outputs kept in $WORK_DIR"
