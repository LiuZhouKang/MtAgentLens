#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# `/api/runs/:id/snapshots` contract check against a real run database.
# =============================================================================
# Copies a run database into the runs root the server scans, starts the
# collector's HTTP server, and asserts the endpoint returns the contract shape
# the frontend consumes:
#
#   [{ snapshot_id, timestamp_ns, nodes, edges, metrics }, ...]
#
# The database can come from scripts/e2e/record-smoke.sh (it produces two
# snapshots). Running the server needs sudo (it starts the eBPF runners even
# with none enabled, because `trace` always attaches the runtime).
#
# Usage: scripts/e2e/snapshots-contract.sh [db-path]
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/collector/build/release/mtagentlens"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/debug/mtagentlens"
SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-1}"
PORT="${SNAPSHOT_PORT:-7397}"
RUN_ID="mtl-snapshot-$$"
RUN_DIR="$ROOT/env/runs/$RUN_ID"
DB="${1:-$(ls -t /tmp/mtl-smoke.*/smoke.db 2>/dev/null | head -1)}"
LOG="$RUN_DIR/trace.log"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "collector binary not found"
[ -f "$DB" ] || die "no run database (run scripts/e2e/record-smoke.sh first, or pass a path)"

mkdir -p "$RUN_DIR/collector"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' cp "$DB" "$RUN_DIR/collector/collector.responsibility.db" \
  || die "cannot copy the database into $RUN_DIR"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' chmod 0644 "$RUN_DIR/collector/collector.responsibility.db"

cleanup() {
  printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$COLLECTOR_PID" 2>/dev/null || true
  rm -rf "$RUN_DIR"
}
COLLECTOR_PID=""
trap cleanup EXIT INT TERM

# `trace` refuses to start with no runner. The process runner with a comm
# that matches nothing starts the server without capturing anything.
# The server resolves `env/runs` relative to the process cwd.
cd "$ROOT"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' "$BIN" trace \
  --ssl false --process true -c mtl-snapshot-no-such-comm \
  -o "$LOG" -q --server --server-port "$PORT" \
  >"$RUN_DIR/server.out" 2>&1 &
SUDO_PID=$!

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
[ -n "$COLLECTOR_PID" ] || die "collector did not start; see $RUN_DIR/server.out"

# Wait for the server to bind.
for _ in $(seq 1 50); do
  curl -s --max-time 2 "http://127.0.0.1:$PORT/api/runs/$RUN_ID/snapshots" >/dev/null && break
  sleep 0.2
done

BODY="$(curl -s --max-time 5 "http://127.0.0.1:$PORT/api/runs/$RUN_ID/snapshots")"
[ -n "$BODY" ] || die "empty response from /api/runs/$RUN_ID/snapshots"

python3 - "$BODY" <<'PY'
import json
import sys

raw = sys.argv[1]
try:
    snapshots = json.loads(raw)
except json.JSONDecodeError as exc:
    print("SNAPSHOTS_CONTRACT=FAIL")
    print(f"  response is not JSON: {exc}: {raw[:200]}")
    sys.exit(1)

if not isinstance(snapshots, list):
    print("SNAPSHOTS_CONTRACT=FAIL")
    print(f"  expected an array, got {type(snapshots).__name__}: {raw[:200]}")
    sys.exit(1)
if not snapshots:
    print("SNAPSHOTS_CONTRACT=FAIL")
    print("  the run database has no graph_snapshots rows")
    sys.exit(1)

required = {"snapshot_id", "timestamp_ns", "nodes", "edges", "metrics"}
for index, snapshot in enumerate(snapshots):
    if not isinstance(snapshot, dict):
        print(f"SNAPSHOTS_CONTRACT=FAIL\n  snapshot {index} is not an object")
        sys.exit(1)
    missing = required - set(snapshot)
    if missing:
        print(f"SNAPSHOTS_CONTRACT=FAIL\n  snapshot {index} missing {sorted(missing)}")
        sys.exit(1)
    if not isinstance(snapshot["nodes"], list) or not isinstance(snapshot["edges"], list):
        print(f"SNAPSHOTS_CONTRACT=FAIL\n  snapshot {index} nodes/edges are not arrays")
        sys.exit(1)

print(f"snapshots: {len(snapshots)}")
print("first snapshot keys:", sorted(snapshots[0]))
print("SNAPSHOTS_CONTRACT=ok")
PY
