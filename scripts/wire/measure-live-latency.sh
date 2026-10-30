#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Live latency: run real agent scenarios and extract the runtime's own
# route/trigger/event-to-alert percentiles from `[diagnostics:json]`.
# =============================================================================
# Requires sudo (eBPF) and a live LLM endpoint (the benchmark drives `claude`).
# Usage:
#   ./scripts/wire/measure-live-latency.sh [out.json] [scenario ...]
# Default scenarios: three benign single-agent tasks.
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/local/history/experiments/live-latency-$(date +%Y%m%d-%H%M%S)/live-latency.json}"
shift || true
SCENARIOS=("$@")
if [ "${#SCENARIOS[@]}" -eq 0 ]; then
  SCENARIOS=(benign-api-doc benign-build-debug benign-code-refactor)
fi

export PATH="$HOME/.local/bin:$PATH"
export MTAGENTLENS_SUDO_PASSWORD="${MTAGENTLENS_SUDO_PASSWORD:-}"
BENCH="$ROOT/env/.venv/bin/mtagentlens-benchmark"
[ -x "$BENCH" ] || BENCH="$(command -v mtagentlens-benchmark)"
[ -n "$BENCH" ] || { echo "ERROR: mtagentlens-benchmark not found" >&2; exit 1; }
BIN="$ROOT/collector/build/release/mtagentlens"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/debug/mtagentlens"
[ -x "$BIN" ] || { echo "ERROR: collector binary not found" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
RESULTS="$OUT.results.jsonl"
: > "$RESULTS"

export PYTHONPATH="$ROOT/env:$ROOT/env/src${PYTHONPATH:+:$PYTHONPATH}"

for scenario in "${SCENARIOS[@]}"; do
  started="$(date +%s)"
  echo "[run] $scenario"
  "$BENCH" run --scenario "$scenario" --mode process --collector --clean-profile \
    > /tmp/mtl-latency-"$scenario".out 2>&1
  rc=$?

  python3 - "$ROOT" "$scenario" "$started" "$rc" "$BIN" >> "$RESULTS" <<'PY'
import hashlib, json, os, sys, time
from pathlib import Path

root, scenario, started, rc, binary = sys.argv[1:]
root = Path(root)
started = float(started)

candidates = []
for run_dir in (root / "env" / "runs").iterdir():
    if not run_dir.is_dir():
        continue
    marker = run_dir / ".scenario_id"
    if not marker.exists():
        continue
    try:
        if marker.read_text().strip() != scenario:
            continue
    except OSError:
        continue
    if run_dir.stat().st_mtime + 5 < started:
        continue
    candidates.append(run_dir)
run_dir = max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

record = {
    "scenario_id": scenario,
    "returncode": int(rc),
    "run_dir": str(run_dir) if run_dir else None,
    "collector_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest() if Path(binary).exists() else None,
    "latency": None,
    "events": None,
}
if run_dir is not None:
    log = run_dir / "collector" / "collector.log"
    if log.exists():
        for line in log.read_text(errors="replace").splitlines():
            if "[diagnostics:json]" not in line:
                continue
            payload = line.split("[diagnostics:json]", 1)[1].strip()
            try:
                diagnostics = json.loads(payload)
            except ValueError:
                continue
            record["latency"] = diagnostics.get("latency")
            record["events"] = {
                "evidence_intents": diagnostics.get("evidence_intents"),
                "actions_produced": diagnostics.get("actions_produced"),
            }
print(json.dumps(record, ensure_ascii=False))
PY
  echo "[done] $scenario rc=$rc"
done

python3 - "$RESULTS" "$OUT" <<'PY'
import hashlib, json, platform, sys
from pathlib import Path

results_path, out_path = sys.argv[1:]
rows = [json.loads(line) for line in Path(results_path).read_text().splitlines() if line.strip()]

def merge(stage):
    samples = [row["latency"][stage] for row in rows if row.get("latency")]
    if not samples:
        return None
    return {
        "runs": len(samples),
        "p50_us": sum(s["p50_us"] or 0 for s in samples) / len(samples),
        "p95_us": max(s["p95_us"] or 0 for s in samples),
        "p99_us": max(s["p99_us"] or 0 for s in samples),
    }

payload = {
    "provenance": {
        "generated_on": "2026-10-19",
        "script": "scripts/wire/measure-live-latency.sh",
        "binary_sha256": rows[0]["collector_sha256"] if rows else None,
        "kernel": platform.release(),
        "note": "Latency is measured inside the runtime; event_to_alert is the lag from the last observed event to alert emission.",
    },
    "runs": rows,
    "summary": {stage: merge(stage) for stage in ("route_event", "trigger", "event_to_alert")},
    "aggregation": "per-stage p50 is the mean of run p50s; p95/p99 are the max across runs",
}
Path(out_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(json.dumps(payload["summary"], indent=2, sort_keys=True))
print(f"wrote {out_path}")
PY
