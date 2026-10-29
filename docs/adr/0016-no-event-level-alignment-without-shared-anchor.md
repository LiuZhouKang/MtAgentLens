---
status: accepted
date: 2026-10-24
---

# 0016 — Event-level alignment stays unavailable until both sides share a verifiable anchor

## Context

A benchmark run produces several distinct identity and clock spaces:

| Space | Producer | Meaning |
|---|---|---|
| `harness_run_id` | benchmark runner | one scheduling attempt |
| `source_run_uuid` | native process ledger | one frozen launch scope |
| `collector_run_id` | collector SQLite | one capture session |
| `runner monotonic ns` | native ledger | Python `time.monotonic_ns()` |
| `kernel monotonic ns` | eBPF | `bpf_ktime_get_ns()` |
| epoch ns | collector timestamps | after `TimestampNormalizer` |

The run binding (`run-binding/v1..v4`) associates the artifacts and proves that a
top-level launched process corresponds to a kernel process: the native actor's
PID namespace/NSpid plus a boot id and the pre/post runner clock samples bound a
kernel `EXEC` witness (`pid`, `start_time_ns`). That is an **identity**
correspondence for the process that the harness launched. It is not a mapping
from a native lifecycle record to a collector action/event, and the binding
carries `event_alignment_available: false`.

Nothing on either side currently provides a shared event identifier:

- the native ledger records lifecycle only (`launch_attempt`,
  `process_started/exited/timeout`, `launch_failed`), with no per-action ids;
- collector events carry kernel process identity and monotonic time, not a
  harness event id;
- internal sub-agent actions inside the launched process have no native
  counterpart at all;
- there is no independent truth channel that could label both sides.

## Options considered

1. **Match by timestamp proximity.** Rejected: two events can be close by
   accident, clocks differ per source, and the resulting pairs cannot be
   falsified. It would fabricate correspondence exactly where the paper needs
   evidence.
2. **Match by string similarity of commands/resources.** Rejected: the native
   side does not record actions, so this compares collector rows to nothing.
3. **Assume one collector event per native launch.** Rejected: a single agent
   run produces many file/network/process events and nested sub-agents.
4. **Wait for a shared anchor.** Accepted: the only honest route is a mechanism
   that makes both sides record the same verifiable token (for example a
   harness-injected event id observed in the process/TLS stream, or an
   independent truth sidecar), plus a contract test that rejects alignments
   without it.

## Decision

1. `event_alignment_available` remains **false** in every run binding until a
   shared anchor exists. The field is a claim, not a placeholder.
2. Top-level process identity alignment may be reported as `matched`; it must
   never be described as event-level, action-level, intent-level, actor-level or
   root-cause alignment.
3. Metrics that need event-level truth — identity attribution accuracy,
   root-cause `@k`, chain P/R — must not be computed from this binding. They
   require independent labels (see the annotation workbench) and remain
   unavailable while labels are synthetic or absent.
4. No heuristic or "best effort" event matcher may be added without a shared
   anchor and a focus test that would fail on a wrong pairing. Timestamp
   proximity alone is not evidence.
5. The limitation text in the binding stays explicit:
   `not_internal_agent_identity`, `not_attack_or_root_cause_truth`,
   plus the clock-witness limitation. If an alignment consumer cannot handle
   `false`, it must fail rather than default to a match.

## Consequences

- Metric CLIs keep refusing score generation when a binding is missing; they do
  not infer alignment from filenames, declared releases or proximity.
- The paper reports correct root-cause/identity numbers only from independent
  annotation, and otherwise states the limitation. Annotation exports keep
  `paper_metrics_available=false`.
- A future alignment implementation is a schema/contract change: it must add the
  anchor's provenance, its failure modes, and a negative test that a mismatched
  pair is rejected.

## Deliberately not decided here

- Which anchor to adopt (harness event id vs independent sidecar) — that choice
  belongs to the experiment protocol, not this repository.
- Whether internal sub-agents can ever be aligned; they need their own truth
  channel.
- Cross-machine alignment; clocks and namespaces there are a separate problem.

## Confirmation

- `compare/tests/test_run_binding.py` asserts `event_alignment_available is
  False` for a real bound run, and `compare/tests/test_kernel_process_alignment.py`
  asserts the alignment scope is `top_level_invocation_process` with
  `event_alignment_available=False`.
- `scripts/evaluation/run_artifacts.py` writes the field unconditionally as
  `false`; changing it requires this ADR to be amended and the tests above to be
  replaced by anchor-specific ones.
- `validate_run_binding` recomputes the binding instead of trusting a stored
  row, so a hand-edited `true` cannot survive verification.
