---
status: accepted
date: 2026-10-18
---

# 0010 — Network event producers: syscall tracepoints, AF_INET only, no invented addresses

## Context

The router has matched `NETWORK_CONNECT` and `NETWORK_SEND` since the action
layer was written, and `ActionClassifier` has produced `NetworkConnect` /
`NetworkSend` candidates. No eBPF program ever emitted either type, so the
action layer was blind to network egress and nothing failed:
`collector/tests/coverage_contract.rs` was added to make such a gap a build-time
fact.

Two decisions had to be made before writing the producer:

1. **Where to hook.** The repository's file-mutation producers use syscall
   tracepoints (`sys_enter_openat`, `sys_enter_unlinkat`, …). The alternative is
   an `fentry` hook on a kernel function, which is faster but ties the program
   to a kernel-internal signature.
2. **What to do with address families that are not decoded.**

## Decision

### Hook: `sys_enter_connect` / `sys_enter_sendto`

- `connect(int fd, const struct sockaddr *addr, socklen_t len)` reads
  `args[1]`; `sendto(fd, buf, len, flags, const struct sockaddr *dest, …)`
  reads `args[4]`.
- These are the same tracepoint family the file-mutation producers use, so one
  filtering model (userspace PID tracking in `process_filter.h`) covers both.
- The tracepoint argument is a user pointer. Every read goes through
  `bpf_probe_read_user`, and only the two fields needed are read:
  `sin_port` (offset 2) and `sin_addr` (offset 4).
- **`addrlen` is checked before decoding.** The kernel ignores the address for
  `sendto(..., addr, 0)` (it uses the connected peer) and rejects a sockaddr
  shorter than its family's struct. A probe that decoded those bytes would
  report a destination the caller never provided, so an `AF_INET` address
  shorter than 16 bytes (or `AF_INET6` shorter than 24, the bytes needed for
  family + port + address) is dropped.
- **Collector mode selection.** The `process` binary starts in PROC mode (1)
  and switches to FILTER mode (2) when it parses `-c`/`-p`. The collector still
  passes `-m 2` explicitly for a filtered run (`build_process_args`), so the
  capture scope does not depend on that parsing side effect; a bare `process`
  run keeps the all-process debugging behaviour.

Rejected: `fentry/__sys_connect` and friends. The syscall tracepoint sees the
copy-from-user path the kernel actually performs and does not depend on an
internal symbol; the existing programs already establish this contract.

### Address families

| Family | Kernel behaviour | Wire shape |
|---|---|---|
| `AF_INET` (2) | parse port + 4 address bytes | `net_op: {family:"AF_INET", host:"a.b.c.d", port:N}` |
| `AF_INET6` (10) | parse port + 16 address bytes | `net_op: {family:"AF_INET6", host:"::1", port:N}` |
| everything else | dropped before ringbuf reserve | no event |

`AF_INET6` is decoded from `sin6_addr` (offset 8, 16 bytes) and rendered with
`inet_ntop`. An address that cannot be read (a shorter `addrlen` or a failed
user copy) is still marked `unsupported_family: true` with no host/port — the
event stays honest, and routing declines to classify it
(`parse_process_event_to_syscall` returns `None`) rather than inventing `:0`.

### No payload

The event carries the destination only — no buffer contents. TLS plaintext is
already captured by `tls_capture`; copying `sendto` buffers would duplicate
that surface (and its secrets) without adding an egress fact.

## Confirmation

- **JSON contract:** `bpftools/tests/test_process_network.c` pins the three
  shapes and escaping of `comm`.
- **Routing/classification contract:** `routing.rs` tests map the wire shape to
  `connect`/`sendto`, run the classifier, and assert `NetworkConnect` /
  `NetworkSend`; a decoded IPv6 host/port maps like IPv4, while an event that
  still carries `unsupported_family` must produce no action.
- **Verifier:** the program loads and attaches (`process -m 0`, real kernel).
- **Producer discovery:** `coverage_contract.rs` now finds the producer. The
  declaration moved from prose to structured fields
  (`unbacked_event_types`, `event_type_aliases`), because the old text scan
  counted a sentence *describing* a producer as a declaration that none exists.
  A declared gap that becomes backed now fails the contract.
- **E2E (outside the harness sandbox):** `scripts/spikes/network-e2e.sh`
  issues 10 TCP connects and 10 UDP sends from a transient systemd unit and
  requires exactly 10 + 10 events naming the target, plus an AF_INET6 attempt
  marked unsupported with no address.
- **Rust chain (real run):** the collector's own `mtagentlens process --
  -m 2 -c python3` (embedded binary, real sudo, transient systemd client)
  emitted 5 `NETWORK_CONNECT` lines with
  `net_op:{family:"AF_INET",host:"127.0.0.1",port:47591}` — the runner, the
  transport decoder and the timestamp normalizer all handle the new event.
- **Volume:** `scripts/spikes/network-noise-measure.sh` measured 400 events for
  400 syscalls (1:1), 0 dropped, 0 malformed JSON in a 200-connect burst;
  baseline in `docs/evaluation/network-noise-budget.json`.
