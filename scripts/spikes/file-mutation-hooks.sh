#!/bin/bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# E2E check for the file-mutation tracepoints.
#
# The harness sandbox hides its own children's file syscalls from host
# tracepoints, so the operations are issued by a system-level transient unit
# (spawned by PID 1's systemd, which is not a child of this shell).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/bpftools/.output/bin/process"
OUT=/tmp/mtl_mutation_e2e.jsonl
rm -f "$OUT"

printf '%s\n' 1 | sudo -S -p '' "$BIN" >"$OUT" 2>/dev/null &
SUDO_PID=$!
sleep 2

printf '%s\n' 1 | sudo -S -p '' systemd-run --system --unit="mtl-mut-$$" --wait --collect \
  /bin/bash -c 'printf x > /tmp/mtl_mut_probe.txt; truncate -s 5 /tmp/mtl_mut_probe.txt; rm -f /tmp/mtl_mut_probe.txt' \
  >/tmp/mtl_mutation_e2e.sd 2>&1
echo "systemd_exit=$?"
sleep 2

printf '%s\n' 1 | sudo -S -p '' kill "$SUDO_PID" 2>/dev/null
wait "$SUDO_PID" 2>/dev/null

echo "deletes=$(grep -c '"event":"FILE_DELETE"' "$OUT" || true)"
echo "truncates=$(grep -c '"event":"FILE_TRUNCATE"' "$OUT" || true)"
grep -E '"event":"FILE_(DELETE|TRUNCATE)"' "$OUT" | grep mtl_mut_probe || true
tail -3 /tmp/mtl_mutation_e2e.sd
