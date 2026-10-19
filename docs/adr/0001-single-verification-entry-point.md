---
status: accepted
date: 2026-10-17
---

# 0001 — One verification entry point, with budgets instead of disables

## Context

The repository had **no CI at all**. Its most expensive recurring defect is that
the tree says one thing and reality says another: 13 files / 5,092 lines that were
never compiled, a 606-line orchestrator with zero callers, 6 of 19
`impl Analyzer` never constructed, documents declaring modules "Active" that never
built, and a `.gitignore` rule that silently kept three eBPF programs out of
version control so that a fresh clone could not build.

Every one of those is mechanically detectable, and none was detected, because
nothing ran on every change.

## Options

1. **No CI** (the status quo). Rejected: it is the cause, not a neutral choice.
2. **A CI job with its own bespoke commands.** Rejected: it drifts from what
   developers run locally, and the two silently diverge — the same failure mode
   as the code/docs drift this repository suffers from.
3. **One entry point (`scripts/verify.sh`) that CI calls verbatim.** Chosen.
4. **`-D warnings` on everything from day one.** Rejected: 295 clippy warnings and
   roughly 198 dead-code items exist today. A gate that starts red gets disabled,
   and a disabled gate is worse than no gate.

## Outcome

`scripts/verify.sh` holds the gates and is the only thing CI runs. Where debt is
real and not yet payable, a gate uses an explicit **budget** that may only
*decrease*:

| Budget | Value | Composition |
|---|---|---|
| clippy warnings (`--all-targets`) | 295 | mostly dead code, which is the P2 deletion work |
| ignored tests | 26 | 3 need a real binary / live OpenClaw; 22 blocked on the retired legacy causal schema (P3) |
| hardcoded `/home/<user>` occurrences | 67 | the benchmark-scenario coupling, moved to profiles in P4 |
| lint-suppression sites | 59 | `#[allow]` in sources plus `[lints]` entries in `Cargo.toml` |

A budget keeps the number visible in every run; an `allow` hides it. Two gates are
unconditional from the start because they were green once fixed: rustfmt, and
module reachability (orphan `.rs` files). A further rule applies to every gate: it
must first assert that what it inspects **exists and is non-empty**, or it will
pass while checking nothing — see
`collector/tests/responsibility_naming_guards.rs`, which inspects a directory that
no longer exists and has been passing for months.

## Confirmation

Automation of any kind (editor hooks, timers, a future CI) runs
`./scripts/verify.sh` verbatim. The four budgets are named constants
at the top of that file, so lowering one is a one-line reviewable change and
raising one requires editing a constant whose name contains "BUDGET".
