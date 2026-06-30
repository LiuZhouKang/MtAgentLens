#!/bin/bash
# =============================================================================
# MtAgentLens Verification Suite Orchestrator
# =============================================================================
# Runs all three verifiers in sequence and produces an aggregate report:
#   1. Python verifier (C0-C6 + Sequential) — Subagent output validation
#   2. Shell verifier (7 layers) — Handoff YAML artifact validation
#   3. Cross-component verifier (E/P/F/D) — eBPF ↔ Rust ↔ Frontend integration
#
# Usage:
#   ./run_verification_suite.sh --run-dir <path> [options]
#
# Options:
#   --run-dir <path>       Run directory (required for Python verifier)
#   --scenario <name>      Scenario name (default: subagents-collaborative)
#   --handoff-prefix <p>   Handoff prefix for shell verifier (default: causal-link)
#   --output <path>        Output file for aggregate report (default: stdout)
#   --skip-python          Skip Python verifier (e.g., if run dir not available)
#   --skip-shell           Skip Shell verifier
#   --skip-cross           Skip Cross-component verifier
#   --verbose              Show detailed per-verifier output
#
# Exit codes:
#   0 = all verifiers pass
#   1 = Python verifier failure
#   2 = Shell verifier failure
#   3 = Cross-component verifier failure
#   4 = Multiple verifier failures (bitmask: 1=python, 2=shell, 4=cross)
# =============================================================================

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HANDOFF_DIR="$REPO_ROOT/.kiro/handoffs"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Defaults ──
RUN_DIR=""
SCENARIO="subagents-collaborative"
HANDOFF_PREFIX="causal-link"
OUTPUT_FILE=""
VERBOSE=false

RUN_PYTHON=true
RUN_SHELL=true
RUN_CROSS=true

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir)       RUN_DIR="$2"; shift 2 ;;
    --scenario)      SCENARIO="$2"; shift 2 ;;
    --handoff-prefix) HANDOFF_PREFIX="$2"; shift 2 ;;
    --output)        OUTPUT_FILE="$2"; shift 2 ;;
    --skip-python)   RUN_PYTHON=false; shift ;;
    --skip-shell)    RUN_SHELL=false; shift ;;
    --skip-cross)    RUN_CROSS=false; shift ;;
    --verbose)       VERBOSE=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Helper: emit to stdout and optionally to file ──
emit() {
  echo "$@"
  if [ -n "$OUTPUT_FILE" ]; then
    echo "$@" >> "$OUTPUT_FILE"
  fi
}

emit_line() {
  emit "────────────────────────────────────────────────────────────────────────"
}

# ── Results tracking ──
PYTHON_STATUS="not_run"
SHELL_STATUS="not_run"
CROSS_STATUS="not_run"
PYTHON_EXIT_CODE=-1
SHELL_EXIT_CODE=-1
CROSS_EXIT_CODE=-1

emit_line
emit "  MtAgentLens Verification Suite Orchestrator"
emit "  Timestamp: $TIMESTAMP"
emit "  Repository: $REPO_ROOT"
emit_line
emit ""

# =============================================================================
# 1. Python Verifier (C0-C6 + Sequential)
# =============================================================================
if [ "$RUN_PYTHON" = true ]; then
  PYTHON_VERIFIER="$SCRIPT_DIR/${SCENARIO}.py"

  if [ ! -f "$PYTHON_VERIFIER" ]; then
    emit "  ⚠ Python verifier not found: $PYTHON_VERIFIER"
    PYTHON_STATUS="error"
    PYTHON_EXIT_CODE=127
  elif [ -z "$RUN_DIR" ]; then
    emit "  ⚠ --run-dir not provided — skipping Python verifier"
    PYTHON_STATUS="skipped"
    PYTHON_EXIT_CODE=0
  elif [ ! -d "$RUN_DIR" ]; then
    emit "  ⚠ Run directory not found: $RUN_DIR — skipping Python verifier"
    PYTHON_STATUS="skipped"
    PYTHON_EXIT_CODE=0
  else
    emit "  Running Python verifier ($SCENARIO)..."
    emit_line

    PYTHON_OUTPUT=$(cd "$REPO_ROOT" && python3 "$PYTHON_VERIFIER" \
      --run-dir "$RUN_DIR" \
      --scenario-dir "$REPO_ROOT/env/scenarios" \
      2>&1)
    PYTHON_EXIT_CODE=$?

    if [ "$VERBOSE" = true ]; then
      echo "$PYTHON_OUTPUT"
    fi

    # Extract summary from JSON output
    PYTHON_STATUS_LINE=$(echo "$PYTHON_OUTPUT" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    status = data.get('status', 'unknown')
    details = data.get('details', '')
    layers = data.get('layers', {})
    layer_summary = ' '.join([f'{k}={v.get(\"status\",\"?\")}' for k,v in layers.items()])
    seq = data.get('sequential', {})
    seq_status = seq.get('status', '?')
    gates = seq.get('gate_decisions', {})
    gate_summary = ' '.join([f'{k}={v}' for k,v in gates.items()])
    print(f'Status: {status}')
    print(f'Details: {details}')
    print(f'Layers: {layer_summary}')
    print(f'Sequential: {seq_status}')
    print(f'Gates: {gate_summary}')
except:
    lines = sys.stdin.read().split('\\n')
    for l in lines[-10:]:
        if 'Results:' in l or 'failed' in l.lower() or 'passed' in l.lower():
            print(l)
" 2>/dev/null)
    emit "$PYTHON_STATUS_LINE"

    if [ "$PYTHON_EXIT_CODE" -eq 0 ]; then
      PYTHON_STATUS="pass"
    elif [ "$PYTHON_EXIT_CODE" -eq 2 ]; then
      PYTHON_STATUS="prompt-fallback"
    else
      PYTHON_STATUS="fail"
    fi

    emit "  Python verifier exit: $PYTHON_EXIT_CODE ($PYTHON_STATUS)"
    emit_line
    emit ""
  fi
else
  emit "  Python verifier: skipped (--skip-python)"
  emit ""
fi

# =============================================================================
# 2. Shell Verifier (Handoff artifact validation)
# =============================================================================
if [ "$RUN_SHELL" = true ]; then
  SHELL_VERIFIER="$HANDOFF_DIR/verify_full_chain.sh"

  if [ ! -f "$SHELL_VERIFIER" ]; then
    emit "  ⚠ Shell verifier not found: $SHELL_VERIFIER"
    SHELL_STATUS="error"
    SHELL_EXIT_CODE=127
  else
    emit "  Running Shell verifier (prefix: $HANDOFF_PREFIX)..."
    emit_line

    SHELL_OUTPUT=$(cd "$REPO_ROOT" && bash "$SHELL_VERIFIER" "$HANDOFF_PREFIX" 2>&1)
    SHELL_EXIT_CODE=$?

    if [ "$VERBOSE" = true ]; then
      echo "$SHELL_OUTPUT"
    fi

    # Extract summary
    SHELL_RESULT_LINE=$(echo "$SHELL_OUTPUT" | grep "Results:" | head -1)
    emit "$SHELL_RESULT_LINE"

    if [ "$SHELL_EXIT_CODE" -eq 0 ]; then
      SHELL_STATUS="pass"
    elif [ "$SHELL_EXIT_CODE" -eq 3 ]; then
      SHELL_STATUS="deliverable-warn"
    else
      SHELL_STATUS="fail"
    fi

    emit "  Shell verifier exit: $SHELL_EXIT_CODE ($SHELL_STATUS)"
    emit_line
    emit ""
  fi
else
  emit "  Shell verifier: skipped (--skip-shell)"
  emit ""
fi

# =============================================================================
# 3. Cross-Component Verifier (E/P/F/D)
# =============================================================================
if [ "$RUN_CROSS" = true ]; then
  CROSS_VERIFIER="$HANDOFF_DIR/verify_cross_component.sh"

  if [ ! -f "$CROSS_VERIFIER" ]; then
    emit "  ⚠ Cross-component verifier not found: $CROSS_VERIFIER"
    CROSS_STATUS="error"
    CROSS_EXIT_CODE=127
  else
    emit "  Running Cross-component verifier..."
    emit_line

    CROSS_OUTPUT=$(cd "$REPO_ROOT" && bash "$CROSS_VERIFIER" 2>&1)
    CROSS_EXIT_CODE=$?

    if [ "$VERBOSE" = true ]; then
      echo "$CROSS_OUTPUT"
    fi

    # Extract summary
    CROSS_RESULT_LINE=$(echo "$CROSS_OUTPUT" | grep "Results:" | head -1)
    emit "$CROSS_RESULT_LINE"

    if [ "$CROSS_EXIT_CODE" -eq 0 ]; then
      CROSS_STATUS="pass"
    elif [ "$CROSS_EXIT_CODE" -eq 2 ]; then
      CROSS_STATUS="fail"
    else
      CROSS_STATUS="warn"
    fi

    emit "  Cross-component verifier exit: $CROSS_EXIT_CODE ($CROSS_STATUS)"
    emit_line
    emit ""
  fi
else
  emit "  Cross-component verifier: skipped (--skip-cross)"
  emit ""
fi

# =============================================================================
# Aggregate Results
# =============================================================================
emit_line
emit "  Aggregate Results"
emit_line
emit "  Python verifier:        $PYTHON_STATUS (exit $PYTHON_EXIT_CODE)"
emit "  Shell verifier:         $SHELL_STATUS (exit $SHELL_EXIT_CODE)"
emit "  Cross-component:        $CROSS_STATUS (exit $CROSS_EXIT_CODE)"
emit_line
emit ""

# Compute bitmask exit code
EXIT_CODE=0
if [ "$PYTHON_STATUS" = "fail" ]; then EXIT_CODE=$((EXIT_CODE + 1)); fi
if [ "$SHELL_STATUS" = "fail" ]; then EXIT_CODE=$((EXIT_CODE + 2)); fi
if [ "$CROSS_STATUS" = "fail" ]; then EXIT_CODE=$((EXIT_CODE + 4)); fi

# Prompt-fallback is informational, not a hard failure
if [ "$PYTHON_STATUS" = "prompt-fallback" ] && [ "$EXIT_CODE" -eq 1 ]; then
  EXIT_CODE=0
fi

if [ "$EXIT_CODE" -eq 0 ]; then
  emit "  ✓ ALL VERIFIERS PASSED"
else
  emit "  ⚠ Verification suite: $EXIT_CODE issue(s) detected"
fi

emit "  Timestamp: $TIMESTAMP"
emit_line

exit "$EXIT_CODE"
