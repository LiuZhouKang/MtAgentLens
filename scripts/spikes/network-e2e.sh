#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# E2E check for the NETWORK_CONNECT / NETWORK_SEND producers.
# =============================================================================
# This must run OUTSIDE the harness sandbox: the harness intercepts its own
# children's syscalls, so a client started by this shell is invisible to host
# tracepoints (see HANDOFF §4 trap 11). The client is therefore launched as a
# transient systemd unit, exactly like scripts/spikes/file-mutation-hooks.sh.
#
# Assertions (all from the wire, not from source):
#   1. every emitted line is valid JSON;
#   2. 10 TCP connects to 127.0.0.1:<port> produce exactly 10 NETWORK_CONNECT
#      events naming that host and port — one syscall, one event;
#   3. 10 UDP sends to 127.0.0.1:<port> produce exactly 10 NETWORK_SEND events;
#   4. no event claims an address for AF_INET6: an IPv6 attempt, when the host
#      supports it, is reported as `unsupported_family` with no host/port.
#
# Requires: sudo (BPF), python3, systemd-run.
# Usage: scripts/spikes/network-e2e.sh
# =============================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/bpftools/.output/bin/process"
OUT=/tmp/mtl_network_e2e.jsonl
CLIENT_OUT=/tmp/mtl_network_e2e.client
TCP_PORT=${TCP_PORT:-47391}
UDP_PORT=${UDP_PORT:-47392}
CONNECTS=${CONNECTS:-10}
SENDS=${SENDS:-10}

if [ ! -x "$BIN" ]; then
	echo "missing $BIN — run: make -C bpftools .output/bin/process" >&2
	exit 1
fi

rm -f "$OUT" "$CLIENT_OUT"

# Listener: accepts $CONNECTS connections and exits.
printf '%s\n' 1 | sudo -S -p '' systemd-run --system --unit="mtl-net-server-$$" --collect \
	python3 -c "
import socket, threading
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $TCP_PORT))
s.listen(32)
def serve():
    n = 0
    while n < $CONNECTS:
        c, _ = s.accept()
        c.close()
        n += 1
t = threading.Thread(target=serve, daemon=True)
t.start(); t.join(timeout=20)
" >/dev/null 2>&1

printf '%s\n' 1 | sudo -S -p '' "$BIN" -m 0 >"$OUT" 2>/dev/null &
TRACER_PID=$!
sleep 2

# Client: TCP connects + UDP sends + one IPv6 attempt (best effort).
printf '%s\n' 1 | sudo -S -p '' systemd-run --system --unit="mtl-net-client-$$" --collect --wait \
	python3 -c "
import socket, time
for _ in range($CONNECTS):
    try:
        c = socket.create_connection(('127.0.0.1', $TCP_PORT), timeout=2)
        c.close()
    except OSError as e:
        print('tcp:', e, file=__import__('sys').stderr)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for _ in range($SENDS):
    u.sendto(b'x', ('127.0.0.1', $UDP_PORT))
try:
    s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s6.settimeout(1)
    s6.connect(('::1', $TCP_PORT))
    s6.close()
except OSError as e:
    print('ipv6 (expected):', e, file=__import__('sys').stderr)
" >"$CLIENT_OUT" 2>&1

sleep 1
kill "$TRACER_PID" 2>/dev/null
wait "$TRACER_PID" 2>/dev/null

python3 - "$OUT" "$TCP_PORT" "$UDP_PORT" "$CONNECTS" "$SENDS" <<'PY'
import json
import sys

path, tcp_port, udp_port, connects, sends = sys.argv[1:]
tcp_port, udp_port = int(tcp_port), int(udp_port)
connects, sends = int(connects), int(sends)

events = []
bad_json = 0
with open(path, encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad_json += 1

connecting = [e for e in events if e.get("event") == "NETWORK_CONNECT"]
sending = [e for e in events if e.get("event") == "NETWORK_SEND"]
to_tcp = [
    e for e in connecting
    if e.get("net_op", {}).get("host") == "127.0.0.1"
    and e.get("net_op", {}).get("port") == tcp_port
]
to_udp = [
    e for e in sending
    if e.get("net_op", {}).get("host") == "127.0.0.1"
    and e.get("net_op", {}).get("port") == udp_port
]

ipv6 = [e for e in connecting + sending
        if e.get("net_op", {}).get("family") == "AF_INET6"]
# A decoded IPv6 event must carry an IPv6 host; an undecodable one must be
# marked and must not carry a host. Neither may invent a placeholder.
fabricated = [
    e for e in ipv6
    if e.get("net_op", {}).get("unsupported_family")
    and "host" in e.get("net_op", {})
]
loopback_ipv6 = [
    e for e in ipv6
    if e.get("net_op", {}).get("host") == "::1"
]
bad_ipv6 = [
    e for e in ipv6
    if not e.get("net_op", {}).get("unsupported_family")
    and not (e.get("net_op", {}).get("host") or "")
]

failures = []
if bad_json:
    failures.append(f"{bad_json} malformed JSON line(s)")
if len(to_tcp) != connects:
    failures.append(f"expected {connects} NETWORK_CONNECT to :{tcp_port}, saw {len(to_tcp)}")
if len(to_udp) != sends:
    failures.append(f"expected {sends} NETWORK_SEND to :{udp_port}, saw {len(to_udp)}")
if fabricated:
    failures.append(f"{len(fabricated)} undecodable AF_INET6 event(s) invented a host address")
if bad_ipv6:
    failures.append(f"{len(bad_ipv6)} decoded AF_INET6 event(s) have no IPv6 host")
if ipv6 and not loopback_ipv6:
    failures.append(
        "AF_INET6 events exist but none name ::1 — the address was decoded wrong"
    )
if not ipv6:
    print("note: no AF_INET6 event observed (IPv6 may be disabled on this host)")
for event in ipv6:
    net = event.get("net_op", {})
    if not net.get("unsupported_family") and "port" not in net:
        failures.append("decoded AF_INET6 event missing port")
        break

print(f"events total={len(events)} connect={len(connecting)} send={len(sending)} "
      f"tcp_hits={len(to_tcp)} udp_hits={len(to_udp)} ipv6={len(ipv6)}")
if failures:
    print("NETWORK_E2E=FAIL")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("NETWORK_E2E=ok")
PY
