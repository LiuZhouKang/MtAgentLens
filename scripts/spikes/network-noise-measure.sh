#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Measure the NETWORK_* producer's event volume.
# =============================================================================
# Produces the numbers in docs/evaluation/network-noise-budget.json. Run it
# outside the harness sandbox (systemd transient unit for the workload).
#
# Method: the tracer runs in the same filter mode the collector uses for
# `record` (mode 2, `-c python3`). The workload is a transient unit doing N
# connects + N UDP sends. We report:
#   - network events emitted for our ports (must equal the syscall count),
#   - every NETWORK_* line regardless of port (background noise from python3),
#   - dropped_events from RINGBUF_STATS (ring-buffer pressure),
#   - total events in the same window (context for the network share).
#
# Usage: scripts/spikes/network-noise-measure.sh [connects]
# =============================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/bpftools/.output/bin/process"
OUT=/tmp/mtl_network_noise.jsonl
ERR=/tmp/mtl_network_noise.err
N=${1:-200}
TCP_PORT=${TCP_PORT:-47401}
UDP_PORT=${UDP_PORT:-47402}

rm -f "$OUT" "$ERR"

printf '%s\n' 1 | sudo -S -p '' systemd-run --system --unit="mtl-noise-server-$$" --collect \
	python3 -c "
import socket, threading
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $TCP_PORT)); s.listen(512)
def serve():
    n = 0
    while n < $N:
        c, _ = s.accept(); c.close(); n += 1
threading.Thread(target=serve, daemon=True).start()
import time; time.sleep(30)
" >/dev/null 2>&1

printf '%s\n' 1 | sudo -S -p '' "$BIN" -m 2 -c python3 >"$OUT" 2>"$ERR" &
TRACER_PID=$!
sleep 2

START_NS=$(date +%s%N)
printf '%s\n' 1 | sudo -S -p '' systemd-run --system --unit="mtl-noise-client-$$" --collect --wait \
	python3 -c "
import socket
for _ in range($N):
    c = socket.create_connection(('127.0.0.1', $TCP_PORT), timeout=5); c.close()
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for _ in range($N):
    u.sendto(b'x', ('127.0.0.1', $UDP_PORT))
" >/dev/null 2>&1
END_NS=$(date +%s%N)

sleep 1
kill "$TRACER_PID" 2>/dev/null
wait "$TRACER_PID" 2>/dev/null

python3 - "$OUT" "$ERR" "$N" "$TCP_PORT" "$UDP_PORT" "$START_NS" "$END_NS" <<'PY'
import json
import sys

path, err_path, n, tcp_port, udp_port, start_ns, end_ns = sys.argv[1:]
n, tcp_port, udp_port = int(n), int(tcp_port), int(udp_port)
elapsed_s = (int(end_ns) - int(start_ns)) / 1e9

events = []
bad = 0
with open(path, encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1

network = [e for e in events if e.get("event") in ("NETWORK_CONNECT", "NETWORK_SEND")]
connects = [e for e in network if e.get("event") == "NETWORK_CONNECT"]
sends = [e for e in network if e.get("event") == "NETWORK_SEND"]
our_tcp = [e for e in connects if e.get("net_op", {}).get("port") == tcp_port
           and e.get("net_op", {}).get("host") == "127.0.0.1"]
our_udp = [e for e in sends if e.get("net_op", {}).get("port") == udp_port
           and e.get("net_op", {}).get("host") == "127.0.0.1"]
# PROCESS_RINGBUF_STATS is written to stderr, not into the JSONL stream.
stats = []
with open(err_path, encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if "PROCESS_RINGBUF_STATS" not in line:
            continue
        try:
            stats.append(json.loads(line))
        except json.JSONDecodeError:
            pass

report = {
    "requested_connects": n,
    "requested_sends": n,
    "observed_connects_to_target": len(our_tcp),
    "observed_sends_to_target": len(our_udp),
    "network_events_total": len(network),
    "all_events_total": len(events),
    "bad_json_lines": bad,
    "elapsed_s": round(elapsed_s, 3),
    "network_events_per_s": round(len(network) / elapsed_s, 1) if elapsed_s else None,
    "dropped_events": max((s.get("dropped_events", 0) for s in stats), default=0),
    "ringbuf_stats_observed": bool(stats),
}
print(json.dumps(report, indent=2))

ok = (bad == 0 and len(our_tcp) == n and len(our_udp) == n and bool(stats))
print(f"NOISE_MEASURE={'ok' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
PY
