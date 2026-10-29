#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# End-to-end smoke test: record -> eBPF -> runner -> routing -> SQLite.
# =============================================================================
# A synthetic "agent" (a python3 symlink named `claude`, so the process filter
# matches) performs one file write, read, rename, delete, one TCP connect and
# one child exec. The collector runs `trace --ssl false --process true`; the
# test then asserts that each of those actions reached the responsibility
# database. No LLM endpoint is needed.
#
# The workload runs as a transient systemd unit: the harness intercepts its
# own children's syscalls, so a direct child would be invisible to the host
# tracepoints (HANDOFF trap 11).
#
# Requires sudo. Usage: scripts/e2e/record-smoke.sh
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/collector/build/release/mtagentlens"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/debug/mtagentlens"
SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-}"
PORT="${SMOKE_PORT:-47891}"
WORK="$(mktemp -d /tmp/mtl-smoke.XXXXXX)"
LOG="$WORK/record.log"
DB="$LOG.responsibility.db"
UNIT_SERVER="mtl-smoke-$$"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "collector binary not found (run: cd collector && cargo build)"
command -v python3 >/dev/null || die "python3 is required"

cleanup() {
  printf '%s\n' "$SUDO_PASS" | sudo -S -p '' systemctl stop "$UNIT_SERVER" 2>/dev/null || true
  if [ -s "$WORK/collector.pid" ]; then
    read -r collector_pid < "$WORK/collector.pid"
    if [[ "$collector_pid" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$collector_pid" 2>/dev/null || true
    fi
  fi
}
trap cleanup EXIT INT TERM

# The agent's process name is the executable's basename. Copy (do not
# symlink) python3 to `claude`: systemd resolves a symlink before exec, so the
# process would be named `python3` and the collector's `-c claude` filter
# would never track it.
cp "$(command -v python3)" "$WORK/claude"
chmod +x "$WORK/claude"

cat > "$WORK/workload.py" <<'PY'
import os, socket, sys, time

port = int(sys.argv[1])
root = sys.argv[2]
src = os.path.join(root, "e2e-source.txt")
dst = os.path.join(root, "e2e-renamed.txt")

with open(src, "w") as handle:
    handle.write("mtagentlens e2e\n")
with open(src) as handle:
    handle.read()
os.rename(src, dst)
os.unlink(dst)

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen(8)
# Several attempts: the first connect can race the userspace tracker that is
# still processing the EXEC event, and one connect is not a reliable sample.
for _ in range(5):
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    client.close()
    time.sleep(0.2)
server.close()

os.system("/bin/true")
time.sleep(1)
PY

echo "collector: $BIN"
echo "workdir:   $WORK"

printf '%s\n' "$SUDO_PASS" | sudo -S -p '' sh -c 'echo "$$" > "$1" || exit; shift; exec "$@"' \
  _ "$WORK/collector.pid" "$BIN" trace \
  --ssl false --process true -c claude -o "$LOG" -q \
  >"$WORK/collector.out" 2>&1 &
SUDO_PID=$!

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

# Wait for the process probe to attach, not a fixed sleep: the collector
# prints this line when the `process` binary is running. Running the workload
# before that loses its EXEC and, with it, every later file/network event
# (the PID was never added to the filter).
for _ in $(seq 1 50); do
  grep -q "bash readline probe attached" "$WORK/collector.out" 2>/dev/null && break
  sleep 0.2
done
sleep 1
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' systemd-run --system --unit="$UNIT_SERVER" \
  --collect --wait "$WORK/claude" "$WORK/workload.py" "$PORT" "$WORK" \
  >"$WORK/workload.out" 2>&1 || die "workload failed; see $WORK/workload.out"

sleep 1
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$COLLECTOR_PID" 2>/dev/null || true
for _ in $(seq 1 60); do
  kill -0 "$COLLECTOR_PID" 2>/dev/null || break
  sleep 0.5
done
wait "$SUDO_PID" 2>/dev/null || true

[ -f "$DB" ] || die "no responsibility database at $DB"

# The collector creates the DB as root with 0600; copy it before reading.
READ_DB="$WORK/smoke.db"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' cp "$DB" "$READ_DB" || die "cannot copy $DB"
printf '%s\n' "$SUDO_PASS" | sudo -S -p '' chown "$(id -u):$(id -g)" "$READ_DB" || true
[ -f "$READ_DB" ] || die "database copy is missing"

python3 - "$READ_DB" "$PORT" "$WORK" <<'PY'
import json
import os
import sqlite3
import sys

db_path, port, work = sys.argv[1], int(sys.argv[2]), sys.argv[3]
conn = sqlite3.connect(db_path)
tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
for required in ("runs", "actions", "process_events"):
    if required not in tables:
        print(f"FAIL missing table {required}")
        sys.exit(1)

actions = dict(conn.execute("select action_type, count(*) from actions group by action_type"))
run_count = conn.execute("select count(*) from runs").fetchone()[0]
network_targets = [
    row[0]
    for row in conn.execute(
        "select normalized_args_json from actions where action_type='NetworkConnect'"
    )
]
port_hit = any(str(port) in (target or "") for target in network_targets)

expected = ["NetworkConnect", "ProcessExec"]
# This harness intercepts its own processes' file syscalls (bpftrace sees the
# same zero; HANDOFF trap 11), so file actions can only be asserted on a
# non-sandboxed host: REQUIRE_FILES=1.
require_files = os.environ.get("REQUIRE_FILES") == "1"
if require_files:
    expected += ["FileOpen", "FileDelete", "FileRename"]
missing = [name for name in expected if actions.get(name, 0) == 0]

print("action types:", json.dumps(actions, sort_keys=True))
print("runs:", run_count, "network port hit:", port_hit)

failures = []
if run_count == 0:
    failures.append("no run row was recorded")
if missing:
    failures.append(f"missing actions: {missing}")
if not port_hit:
    failures.append(f"NetworkConnect did not record port {port}")

if require_files:
    # Open flags prove the requested access mode; an open is not evidence of
    # an actual read/write syscall. The process probe reports FileOpen.
    source = os.path.join(work, "e2e-source.txt")
    destination = os.path.join(work, "e2e-renamed.txt")
    observed = [
        (kind, json.loads(args))
        for kind, args in conn.execute(
            "SELECT action_type, normalized_args_json FROM actions WHERE outcome='succeeded'"
        )
    ]
    def file_action(kind, paths, flags=None):
        return any(
            action == kind and all({"Path": path} in args for path in paths)
            and (flags is None or any(
                isinstance(arg, dict) and "OpenFlags" in arg and flags(arg["OpenFlags"])
                for arg in args
            ))
            for action, args in observed
        )
    checks = {
        "readonly open": file_action("FileOpen", [source], lambda value: value & os.O_ACCMODE == os.O_RDONLY),
        "truncate open": file_action("FileOpen", [source], lambda value: bool(value & os.O_TRUNC)),
        "rename endpoints": file_action("FileRename", [source, destination]),
        "delete destination": file_action("FileDelete", [destination]),
    }
    for label, passed in checks.items():
        if not passed:
            failures.append(f"missing successful workload file operation: {label}")
    print("workload file operations:", json.dumps(checks, sort_keys=True))

if not require_files:
    print("note: file actions not asserted in this sandbox (REQUIRE_FILES=1 on a host)")

if failures:
    print("RECORD_SMOKE=FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("RECORD_SMOKE=ok")
PY
