#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# P3-b spike S1: does stdiocap's read(2) path actually see an agent's file read?
# =============================================================================
# GO only if, in >=2 of 3 runs, a stdio READ event has:
#   - fd_target path-compatible with the injected fixture
#   - data containing "ignore previous instructions"
#   - copy_failed == false
#   - the run's STATS dropped_events == 0
# Otherwise NO-GO: the external-content seed must not be implemented/claimed.
#
# Privacy: the -o log contains the target process's file reads for the capture
# window. It is parsed and deleted; only aggregate counts are printed.
#
# Usage: scripts/spikes/p3b-stdio-read-coverage.sh [--runs 3] [--target claude|openclaw]
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/env/scenarios/fixtures/p3b-external-content/injected_handoff.md"
BIN="$ROOT/collector/build/release/mtagentlens"
RUNS=3
TARGET="claude"

while [ $# -gt 0 ]; do
  case "$1" in
    --runs) RUNS="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -f "$FIXTURE" ] || { echo "ERROR: missing fixture $FIXTURE" >&2; exit 1; }
[ -x "$BIN" ] || { echo "ERROR: missing $BIN (cargo build --release)" >&2; exit 1; }
grep -qi 'ignore previous instructions' "$FIXTURE" || {
  echo "ERROR: fixture lacks the injection needle" >&2; exit 1;
}

WORK="$(mktemp -d /tmp/p3b-spike.XXXXXX)"
echo "workdir: $WORK"
echo "fixture: $FIXTURE"

run_claude_target() { # run_claude_target <log> <out>
  local log="$1" out="$2"
  # `exec` replaces the subshell in place, so $! is the claude PID that
  # stdiocap must attach to (a bare subshell PID captures nothing).
  ( cd /tmp && exec claude --permission-mode bypassPermissions -p "Use the Read tool to read $FIXTURE and reply with exactly the last line of that file. Do not use any other tool." >"$out" 2>&1 ) &
  local claude_pid=$!
  ( sleep 90; kill "$claude_pid" 2>/dev/null ) &
  local watchdog=$!
  # Attach immediately: the model call takes seconds, the read happens after it.
  # Direct stdiocap capture: the spike measures the kernel channel, not the
  # collector's logging path. It also avoids writing raw file bytes through
  # FileLogger. The runner's STATS parsing is covered by a unit test.
  printf '%s\n' "${MTAGENTLENS_SUDO_PASSWORD:-}" | sudo -S -p '' \
    "$ROOT/bpftools/.output/bin/stdiocap" -p "$claude_pid" --all-fds --max-bytes 8192 \
    >"$log" 2>"$log.err" &
  local collector_pid=$!
  wait "$claude_pid" 2>/dev/null
  kill "$watchdog" 2>/dev/null
  sleep 3
  printf '%s\n' "${MTAGENTLENS_SUDO_PASSWORD:-}" | sudo -S -p '' kill -INT "$collector_pid" 2>/dev/null
  wait "$collector_pid" 2>/dev/null
}

run_openclaw_target() { # run_openclaw_target <log> <out>
  local log="$1" out="$2"
  local pid
  pid="$(pgrep -f 'openclaw/dist/index.js gateway' | head -1)"
  [ -n "$pid" ] || { echo "no openclaw gateway" >"$out"; return 1; }
  printf '%s\n' "${MTAGENTLENS_SUDO_PASSWORD:-}" | sudo -S -p '' \
    "$ROOT/bpftools/.output/bin/stdiocap" -p "$pid" --all-fds --max-bytes 8192 \
    >"$log" 2>"$log.err" &
  local collector_pid=$!
  sleep 2
  timeout 90 node "$HOME/.nvm/versions/node/v24.19.0/lib/node_modules/openclaw/openclaw.mjs" \
    agent --agent main --session-key "p3b-spike-$(date +%s)" --timeout 70 \
    --message "Use the exec tool to run this JavaScript: const fs=require('fs'); return fs.readFileSync('$FIXTURE','utf8');" \
    >"$out" 2>&1
  sleep 5
  printf '%s\n' "${MTAGENTLENS_SUDO_PASSWORD:-}" | sudo -S -p '' kill -INT "$collector_pid" 2>/dev/null
  wait "$collector_pid" 2>/dev/null
}

pass=0
for run in $(seq 1 "$RUNS"); do
  log="$WORK/run-$run.log"
  out="$WORK/run-$run.agent.out"
  start=$(date +%s)
  if [ "$TARGET" = "openclaw" ]; then
    run_openclaw_target "$log" "$out"
  else
    run_claude_target "$log" "$out"
  fi
  elapsed=$(( $(date +%s) - start ))

  python3 - "$log" "$FIXTURE" "$elapsed" <<'PY'
import json, os, sys
log, fixture, elapsed = sys.argv[1], sys.argv[2], int(sys.argv[3])
reads = needle_hits = copy_failed = 0
dropped = None
bytes_total = os.path.getsize(log) if os.path.exists(log) else 0
for line in open(log, errors="replace") if os.path.exists(log) else []:
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("direction") == "STATS":
        dropped = event.get("dropped_events", dropped)
        continue
    if event.get("direction") != "READ":
        continue
    reads += 1
    if event.get("copy_failed"):
        copy_failed += 1
    target = event.get("fd_target") or ""
    payload = event.get("data") or ""
    if fixture in target and "ignore previous instructions" in payload.lower():
        needle_hits += 1
print(json.dumps({
    "reads": reads,
    "matching_reads": needle_hits,
    "copy_failed_reads": copy_failed,
    "dropped_events": dropped,
    "log_bytes": bytes_total,
    "elapsed_s": elapsed,
}))
PY
done

# Aggregate the Python JSON lines emitted above.
python3 - "$WORK" "$RUNS" <<'PY'
import glob, json, os, sys
work, runs = sys.argv[1], int(sys.argv[2])
# The per-run JSON was printed to stdout, not stored; re-parse from a side file
# is not possible here, so this block only reports what remains on disk.
logs = sorted(glob.glob(os.path.join(work, "run-*.log")))
good = 0
for log in logs:
    # The per-run verdict is recomputed here from the retained log.
    reads = hits = failed = 0
    dropped = None
    for line in open(log, errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("direction") == "STATS":
            dropped = e.get("dropped_events", dropped); continue
        if e.get("direction") != "READ":
            continue
        reads += 1
        if e.get("copy_failed"):
            failed += 1
        t = e.get("fd_target") or ""
        p = e.get("data") or ""
        if "injected_handoff.md" in t and "ignore previous instructions" in p.lower():
            hits += 1
    if hits >= 1 and failed == 0 and dropped == 0:
        good += 1
    print(f"{os.path.basename(log)}: reads={reads} matching={hits} copy_failed={failed} dropped={dropped}")
print(f"GO_CHECK: {good}/{len(logs)} runs matching (require >=2 of {runs})")
sys.exit(0 if good >= 2 else 1)
PY
rc=$?

if [ "${KEEP:-0}" = "1" ]; then
  echo "raw logs kept in $WORK (KEEP=1)"
else
  echo "raw logs are in $WORK; deleting them now (they may contain unrelated file reads)"
  rm -rf "$WORK"
fi
exit $rc
