#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# One frozen binary, one small-sample campaign.
# =============================================================================
# Freezes the release binary, records its SHA, then runs the requested stages
# against that same binary:
#   fpr      20 benign scenarios (actionable FPR)
#   latency  3 real agent sessions (runtime percentiles)
#   audit    action-trace construct-validity audit
#   overhead 5 idle + 5 load runs with the Observer off (optional; costs more)
#
# Usage: scripts/wire/run-small-sample.sh [--with-overhead] [stage ...]
# Output: docs/evaluation/fresh-<date>/ (manifest.json + stage artifacts)
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATE="$(date +%Y-%m-%d)"
OUT="${MTL_SAMPLE_OUT:-$ROOT/docs/evaluation/fresh-$DATE}"
WITH_OVERHEAD=0
STAGES=()
for arg in "$@"; do
  case "$arg" in
    --with-overhead) WITH_OVERHEAD=1 ;;
    *) STAGES+=("$arg") ;;
  esac
done
if [ "${#STAGES[@]}" -eq 0 ]; then
  STAGES=(fpr latency audit)
fi

export PATH="$HOME/.local/bin:$PATH"
export MTAGENTLENS_SUDO_PASSWORD="${MTAGENTLENS_SUDO_PASSWORD:-}"
BIN="$ROOT/collector/build/release/mtagentlens"

mkdir -p "$OUT"
echo "output: $OUT"

# ── Freeze ────────────────────────────────────────────────────────────────
echo "[freeze] building release binary"
( cd "$ROOT/collector" && cargo build --release ) || { echo "build failed" >&2; exit 1; }
[ -x "$BIN" ] || { echo "missing $BIN" >&2; exit 1; }
SHA="$(sha256sum "$BIN" | awk '{print $1}')"
echo "[freeze] $BIN sha256=$SHA"
printf '%s\n' "$SHA" > "$OUT/binary.sha256"

run_stage() {
  local stage="$1"
  case "$stage" in
    fpr)
      local dir="$OUT/fpr"
      mkdir -p "$dir"
      echo "[fpr] 20 benign scenarios"
      "$ROOT/scripts/wire/run-benign-fpr.sh" "$dir/runs"
      local rc=$?
      python3 "$ROOT/scripts/wire/summarize_fpr.py" "$dir/runs" \
        --out-dir "$dir" --name fpr-frozen || true
      return $rc
      ;;
    latency)
      echo "[latency] 3 live sessions"
      "$ROOT/scripts/wire/measure-live-latency.sh" "$OUT/live-latency.json"
      ;;
    audit)
      echo "[audit] action traces"
      python3 "$ROOT/compare/scripts/audit/audit_action_traces.py" \
        --out "$OUT/action-trace-audit.json"
      ;;
    overhead)
      if [ "$WITH_OVERHEAD" != "1" ]; then
        echo "[overhead] skipped (pass --with-overhead)"
        return 0
      fi
      echo "[overhead] observer-off idle x5"
      for i in 1 2 3 4 5; do
        MTL_LITE=1 MTL_IDLE_SECONDS=30 "$ROOT/scripts/wire/measure-overhead.sh" 30 \
          "$OUT/overhead-observer-off-idle-$i.json" || return 1
      done
      echo "[overhead] observer-off load x5 (5 turns each)"
      for i in 1 2 3 4 5; do
        MTL_LITE=1 MTL_WORKLOAD_RUNS=5 "$ROOT/scripts/wire/measure-overhead.sh" 45 \
          "$OUT/overhead-observer-off-load-$i.json" || return 1
      done
      ;;
    *)
      echo "unknown stage: $stage" >&2
      return 2
      ;;
  esac
}

failures=()
for stage in "${STAGES[@]}"; do
  echo "── stage: $stage"
  if ! run_stage "$stage"; then
    failures+=("$stage")
  fi
done

python3 - "$OUT" "$SHA" "$BIN" "${STAGES[@]}" <<'PY'
import json, os, platform, sys
from pathlib import Path
out, sha, binary, *stages = sys.argv[1:]
payload = {
    "generated_on": __import__("datetime").datetime.now().astimezone().isoformat(),
    "collector_binary": binary,
    "collector_sha256": sha,
    "stages": stages,
    "environment": {
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    },
    "note": "Small sample only. Do not promote to the paper's final table without the full run and the user's budget confirmation.",
}
Path(out, "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(f"[manifest] {out}/manifest.json")
PY

if [ "${#failures[@]}" -gt 0 ]; then
  echo "FAILED stages: ${failures[*]}" >&2
  exit 1
fi
echo "small-sample campaign: ok"
