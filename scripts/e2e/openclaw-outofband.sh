#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# E2E: an OpenClaw `tool.call` row becomes an OutOfBandAsserted intent.
# =============================================================================
# Self-contained: builds a minimal `openclaw-agent.sqlite` fixture, starts the
# collector with the out-of-band runner (system runner only; the collector's
# preflight still requires CAP_BPF, so sudo is needed), appends a tool.call
# row, and asserts the intent reached SQLite.
#
# Usage:
#   ./scripts/e2e/openclaw-outofband.sh
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="${MTAGENTLENS_BIN:-$ROOT/collector/build/release/mtagentlens}"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/debug/mtagentlens"
[ -x "$BIN" ] || { echo "collector binary not found (build it first)" >&2; exit 1; }

SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-}"
WORK=$(mktemp -d /tmp/mtl-ob-e2e.XXXXXX)
STATE="$WORK/state"
LOG="$WORK/trace.log"
mkdir -p "$STATE/agents/main/agent"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

python3 - "$STATE" <<'PY'
import sqlite3, sys
path = f"{sys.argv[1]}/agents/main/agent/openclaw-agent.sqlite"
c = sqlite3.connect(path)
c.execute(
    "CREATE TABLE trajectory_runtime_events ("
    "session_id TEXT, seq INTEGER, run_id TEXT, event_json TEXT, created_at INTEGER)"
)
old = (
    '{"traceSchema":"openclaw-trajectory","schemaVersion":1,"type":"session.started",'
    '"ts":"2026-10-01T00:00:00.000Z","sessionKey":"agent:main:main"}'
)
c.execute(
    "INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)",
    ("s0", 0, "r0", old, 1),
)
c.commit()
c.close()
PY

printf '%s\n' "$SUDO_PASS" | sudo -S -p '' env \
  MTAGENTLENS_OPENCLAW_STATE="$STATE" \
  MTAGENTLENS_OPENCLAW_AGENTS=main \
  MTAGENTLENS_OPENCLAW_POLL_MS=100 \
  timeout -s INT 15 "$BIN" trace --system --system-interval 1 -o "$LOG" \
  >"$WORK/out" 2>&1 &
JOB=$!
sleep 4

python3 - "$STATE" <<'PY'
import json, sqlite3, sys, time
path = f"{sys.argv[1]}/agents/main/agent/openclaw-agent.sqlite"
event = {
    "traceSchema": "openclaw-trajectory",
    "schemaVersion": 1,
    "type": "tool.call",
    "ts": "2026-10-19T13:00:00.000Z",
    "sessionKey": "agent:main:main",
    "runId": "mtl-e2e",
    "data": {"toolCallId": "mtl-e2e", "name": "exec",
             "args": {"command": "echo mtl-openclaw-e2e"}},
}
c = sqlite3.connect(path)
seq = c.execute("SELECT coalesce(max(seq),0)+1 FROM trajectory_runtime_events").fetchone()[0]
c.execute(
    "INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)",
    ("mtl-e2e", seq, "mtl-e2e", json.dumps(event), int(time.time() * 1000)),
)
c.commit()
c.close()
PY

wait "$JOB" 2>/dev/null || true

# SIGINT flushes the writer asynchronously; wait for the DB to settle.
for _ in $(seq 1 20); do
  [ -f "$LOG.responsibility.db" ] && break
  sleep 0.5
done
if [ ! -f "$LOG.responsibility.db" ]; then
  echo "responsibility DB was not created" >&2
  ls -l "$WORK" >&2
  tail -20 "$WORK/out" >&2
  exit 1
fi

echo "--- diagnostics ---"
grep -E 'diagnostics:json|DEGRADED|flushed cleanly' "$WORK/out" | tail -2

echo "--- intents ---"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' cp "$LOG.responsibility.db" "$WORK/ints.db"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' chown "$(id -u):$(id -g)" "$WORK/ints.db"
python3 - "$WORK/ints.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
rows = list(c.execute(
    "SELECT tool_name, authority_source, parse_state FROM intents"
))
for row in rows:
    print(row)
ok = any(
    r[0] == "exec" and r[1] == "OutOfBandAsserted" for r in rows
)
print("ASSERT", "ok" if ok else "FAILED")
raise SystemExit(0 if ok else 1)
PY
