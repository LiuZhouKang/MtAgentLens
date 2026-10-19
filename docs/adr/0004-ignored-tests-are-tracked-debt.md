---
status: accepted
date: 2026-10-17
---

# 0004 — 26 ignored tests are tracked debt, not a solution

## Context

`cargo test` was red: 11 of the 41 tests in `bin/replay` failed. The failure was
initially opaque ("no such table: agent_instances"), so the mechanism was traced:

- `bin/replay` targets the **legacy causal schema** — `open_causal_db` /
  `trace.log.causal.db` (`replay.rs:622`), tables `agent_instances`,
  `intent_events`.
- The collector **no longer produces that artifact**. Real runs emit
  `collector.responsibility.db` only (the 27-table `schema.rs` layout); **no
  `*.causal.db` exists anywhere in the repository**.
- The 11 failures are therefore not fixture noise: the replay engine asserts
  replayed-vs-live parity against a schema nothing produces any more.

Measuring the whole suite further revealed that this obsolescence is not confined
to `bin/replay`: **11 `server::web` tests** guarding alert/traceback/causal-evidence
behaviour carry a bare `#[ignore]` for the same reason. So the retired causal
schema silently disables **22 tests**, which is why nobody noticed for months.

## Options

1. **Fix it now.** Rejected for this phase: retargeting replay and the web alert
   path onto the live schema is the schema-unification item, and doing it before
   the byte-lossless transport work (P1) means rewriting against data that is
   still being dropped. Sequencing is by data dependency.
2. **Delete the failing tests.** Rejected: it destroys the only assertions that
   describe the replay contract, and hides the fact that a *product capability*
   (`mtagentlens replay`, invoked by `env/.../collector_manager.py` run-lab flows)
   is broken.
3. **Leave them failing.** Rejected: a red suite makes CI impossible, and CI is
   the precondition for every other mechanism.
4. **`#[ignore = "reason"]`, with a budget and a reason gate.** Chosen.

## Outcome

- The 11 `bin/replay` tests carry a `KNOWN-BROKEN` note plus
  `#[ignore = "replay targets the legacy causal schema …"]`.
- The 11 `server::web` tests gained the same explicit reason (they previously had
  a bare `#[ignore]`, i.e. invisible debt).
- `bin/replay` is recorded under `known_limitations` in `docs/capabilities.json`.
- A fixture-direction change made during diagnosis (using `open_causal_db` in the
  test fixtures) was **reverted**: the eventual fix moves replay onto the *live*
  schema, so fixtures must stay on `open_db`.

## Confirmation

Two gates in `scripts/verify.sh`: the ignored-test **budget** (≤ 26, may only
decrease) and the **reason gate** (a bare `#[ignore]` fails the build). Removing
the debt means lowering the budget in the same commit that retargets replay.
