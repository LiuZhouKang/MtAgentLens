#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Benign-scenario false-positive measurement (P0.5 deliverable 6)
# =============================================================================
# Runs every scenario under env/scenarios/benign/ with the real eBPF collector
# and records the alert rows straight from SQLite. Requires sudo (eBPF) and a
# live LLM endpoint; therefore this is NOT part of scripts/verify.sh. The
# committed artifact docs/evaluation/fpr-baseline.json is the measured record.
#
# Usage:
#   ./scripts/wire/run-benign-fpr.sh [output_dir]
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/env/fpr-runs/$(date +%Y%m%d-%H%M%S)}"

export PATH="$HOME/.local/bin:$PATH"
export MTAGENTLENS_SUDO_PASSWORD="${MTAGENTLENS_SUDO_PASSWORD:-1}"

mkdir -p "$OUT"
echo "output: $OUT"

# The runner imports the benchmark's isolation helper, so use the project
# venv (editable install) rather than the system interpreter.
PY="$ROOT/env/.venv/bin/python"
[ -x "$PY" ] || PY=python3
export PYTHONPATH="$ROOT/env:$ROOT/env/src${PYTHONPATH:+:$PYTHONPATH}"
"$PY" "$ROOT/scripts/wire/fpr_runner.py" "$OUT"
rc=$?
echo "fpr_runner rc=$rc"
exit $rc
