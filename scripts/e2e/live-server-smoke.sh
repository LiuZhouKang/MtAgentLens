#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Live server smoke: trace --server -> /api/runs/active -> live artifacts -> SSE
# =============================================================================
# Proves the real-time contract without a browser:
#   1. `<log>.responsibility.db` is discovered as the `live` run;
#   2. /alerts, /snapshots, /evidence, /graph all answer for that run;
#   3. /api/live/events emits SSE frames while the collector is running.
# Requires sudo (eBPF). Usage: scripts/e2e/live-server-smoke.sh
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/collector/build/debug/mtagentlens"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/release/mtagentlens"
SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-}"
PORT="${LIVE_SMOKE_PORT:-47892}"
WORK="$(mktemp -d /tmp/mtl-live.XXXXXX)"
LOG="$WORK/live.log"
UNIT="mtl-live-$$"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "collector binary not found (run: cd collector && cargo build)"
command -v curl >/dev/null || die "curl is required"

cleanup() {
  printf '%s\n' "$SUDO_PASS" | sudo -S -p '' systemctl stop "$UNIT" 2>/dev/null || true
  if [ -s "$WORK/collector.pid" ]; then
    read -r collector_pid < "$WORK/collector.pid"
    if [[ "$collector_pid" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$collector_pid" 2>/dev/null || true
    fi
  fi
}
trap cleanup EXIT INT TERM

echo "collector: $BIN"
echo "workdir:   $WORK"

printf '%s\n' "$SUDO_PASS" | sudo -S -p '' sh -c 'echo "$$" > "$1" || exit; shift; exec "$@"' \
  _ "$WORK/collector.pid" "$BIN" trace \
  --ssl false --process true -c claude -o "$LOG" -q \
  --server --server-port "$PORT" >"$WORK/collector.out" 2>&1 &
SUDO_PID=$!

# Wait for the HTTP server, not a fixed sleep.
ready=""
for _ in $(seq 1 100); do
  if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.2
done
[ -n "$ready" ] || die "web server did not start; see $WORK/collector.out"

# Read the PID recorded immediately before exec, including nested sudo setups.
COLLECTOR_PID=""
for _ in $(seq 1 50); do
  if [ -s "$WORK/collector.pid" ]; then
    read -r COLLECTOR_PID < "$WORK/collector.pid"
    break
  fi
  sleep 0.2
done
[[ "$COLLECTOR_PID" =~ ^[0-9]+$ ]] || die "collector PID unavailable; see $WORK/collector.out"

# The HTTP server becomes ready before the process probe has attached.
probe_ready=""
for _ in $(seq 1 100); do
  if grep -q "bash readline probe attached" "$WORK/collector.out"; then
    probe_ready=1
    break
  fi
  sleep 0.2
done
[ -n "$probe_ready" ] || die "process probe did not attach; see $WORK/collector.out"

# A tiny CLI workload (no LLM) so the DB gets a run row and events.
cp "$(command -v python3)" "$WORK/claude"
chmod +x "$WORK/claude"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' systemd-run --system --unit="$UNIT" \
  --collect --wait "$WORK/claude" -c 'import time; time.sleep(1)' \
  >"$WORK/workload.out" 2>&1 || die "workload failed; see $WORK/workload.out"

# Wait for actual production data, rather than accepting an empty API shape.
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/runs/live/evidence" \
      | python3 -c 'import json,sys; sys.exit(not bool(json.load(sys.stdin).get("actions")))' 2>/dev/null; then
    break
  fi
  sleep 0.2
done

curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/runs/active" >"$WORK/active.json" \
  || die "/api/runs/active failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/runs/live/alerts" >"$WORK/alerts.json" \
  || die "/api/runs/live/alerts failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/runs/live/snapshots" >"$WORK/snapshots.json" \
  || die "/api/runs/live/snapshots failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/runs/live/evidence" >"$WORK/evidence.json" \
  || die "/api/runs/live/evidence failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/runs/live/graph" >"$WORK/graph.json" \
  || die "/api/runs/live/graph failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/runs/live/db" >"$WORK/live.db" \
  || die "/api/runs/live/db failed"
# The embedded frontend must be served by the same process.
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/" >"$WORK/index.html" \
  || die "frontend index failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/console" >"$WORK/console.html" \
  || die "frontend /console failed"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/overview" >"$WORK/overview.html" \
  || die "frontend /overview failed"

# SSE: the heartbeat is emitted immediately, so a short read is deterministic.
curl -sS --max-time 3 "http://127.0.0.1:$PORT/api/live/events" >"$WORK/sse.txt" 2>/dev/null || true

python3 - "$WORK" <<'PY'
import json
import os
import sys

work = sys.argv[1]


def load(name):
    with open(os.path.join(work, name), encoding="utf-8") as handle:
        return json.load(handle)


active = load("active.json")
failures = []
if active.get("run_id") != "live":
    failures.append(f"/api/runs/active did not report the live run: {active!r}")
if active.get("state") != "running":
    failures.append(f"live run state is not running: {active.get('state')!r}")

alerts = load("alerts.json")
snapshots = load("snapshots.json")
evidence = load("evidence.json")
graph = load("graph.json")
if not isinstance(alerts, list):
    failures.append("alerts is not an array")
if not isinstance(snapshots, list):
    failures.append("snapshots is not an array")
for key in ("evidence", "intents", "actions", "non_links"):
    if not isinstance(evidence.get(key), list):
        failures.append(f"evidence.{key} is not an array")
if not evidence.get("actions"):
    failures.append("workload produced no persisted actions")
if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
    failures.append("graph is missing nodes/edges arrays")

with open(os.path.join(work, "sse.txt"), encoding="utf-8", errors="replace") as handle:
    sse = handle.read()
if "event: heartbeat" not in sse and "event: event" not in sse:
    failures.append("SSE stream produced no heartbeat/event frame")

print("active:", active)
print("alerts:", len(alerts), "snapshots:", len(snapshots),
      "evidence:", len(evidence["evidence"]), "intents:", len(evidence["intents"]),
      "actions:", len(evidence["actions"]), "graph nodes:", len(graph["nodes"]))
print("sse bytes:", len(sse))

if failures:
    print("LIVE_SMOKE=FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("LIVE_SMOKE=ok")
PY
