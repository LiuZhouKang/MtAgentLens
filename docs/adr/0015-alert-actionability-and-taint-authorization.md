---
status: accepted
date: 2026-10-19
---

# 0015 — Actionable alerts are `Unsafe`; taint claims require an unauthorized share

## Context

P1.1 of the handoff recorded two product facts:

- `Warning+` (severity ∈ {Warning, Critical}) had TPR 0.9851 but FPR **0.8947**
  (17/19 benign scenarios) on the 2026-07 FuxiBench corpus. The engine score in
  those runs was `0.0`/`direct_safe`; the Warning came from `AlertBuilder`'s
  branch "claims observed, but engine score below the unsafe threshold —
  retaining Warning for review" (`alert_builder.rs`).
- `TaintedContextUse` fired in 15/19 benign scenarios (FPR 0.789,
  Wilson 0.567–0.915) and 59/67 attacks. No authorization check existed: the
  intended gate (`judgment/context_trust.rs`) had zero callers and was deleted
  in ADR-0013.

The same word was defined oppositely in committed artifacts:
`standard_evaluation_v5.json` called Warning+ "actionable", while
`fpr-baseline.json` and `summarize_fpr.py` called `decision=Unsafe`
"actionable". A product cannot have two definitions of the thing operators act
on.

First-hand re-measurement (read-only SQL over the 86 tracked DBs, this session):

| Fact | Value |
|---|---|
| Benign scenarios with any claim | 17/19 (15 TaintedContextUse + 2 PolicyViolation) |
| Benign TaintedContextUse confidence | 15/15 are `Inferred`, 0.60–0.67 (maximum < 0.68) |
| Attack TaintedContextUse claim states | Inferred 532, Confirmed 208, Observed 22, Ambiguous 51; only 61 rows at exactly 0.90 |
| Benign with `decision=Unsafe` | 0/19 |
| **Counterfactual:** TaintedContextUse at confidence ≥ 0.70 | benign 0/19, attack 43/67 (confidence gate only) |
| **Counterfactual:** Warning+ after demoting the safe-score claim branch | benign 0/19, attack 63/67 |

Every corpus DB has **zero** `delegation_records` and zero
`subagent_boundaries` rows, so an authorization gate cannot be *measured* on
that corpus; it is unit-tested and awaits the P1.2 recollection.

## Decision

1. **One actionable definition.** `actionable := decision == Unsafe`. It is
   carried on the alert wire (`ResponsibilityAlert.actionable`, derived from
   `decision`, recomputed on DB read, never persisted). `Warning+` is a review
   signal and is named `warning_plus` in the evaluation artifact; it is never
   called actionable.
2. **A claim is an observation, not a verdict.** When claims exist but the
   engine score is in the safe range (`<= 0.15`), the alert is
   `Uncertain / Info`, not `Uncertain / Warning`. The claim is still persisted
   and visible; only its presentation changes. This makes Warning+ usable
   (counterfactual: benign 0/19, attack 63/67) without hiding evidence.
3. **`TaintedContextUse` requires four conditions, not one:**
   - the boundary crosser is the taint **receiver** (`taint.target ==
     edge.source`), not the writer;
   - the crossing and the taint edge share the observation (`evidence_refs`
     intersection — the P1 amplification fix);
   - the share is **unauthorized**: no observed direct process-spawn
     delegation from writer to reader exists. Direction is strict — a
     parent→child delegation authorizes the child to consume the parent's
     context, but the child's output flowing back to the parent stays
     unauthorized (that is the multi-agent injection channel);
   - the crossing reaches `Observed` confidence (`>= 0.70`). Weaker crossings
     keep their generic `PolicyViolation` classification.
   Authorized or unattributed shares remain graph facts (`TaintPropagatesTo` /
   `ContextFlowsFrom`) but do not produce this claim.
4. **The taint policy boundary uses the same predicate.** A same-agent read of
   its own contaminated file (or a read before contamination) no longer mints a
   `PolicyBoundaryCrossing`: step 5 asks `TaintTracker::check_access_from`
   instead of passing every contaminated path.
5. **A taint source must be a concrete file.** Empty strings, `/`,
   `(unknown path)`, `.`, `..` and trailing-slash directories are rejected. The
   July corpus recorded `/` as the contaminated artifact in 15/19 benign runs.
6. **The display profile describes the source, not the outcome.** An engine
   `Safe` decision is `SafeBaseline`; any other engine-generated judgment keeps
   the historical `UnsafeByEngine` wire value, including an `Uncertain` alert
   caused by a claim. The profile names *who judged* (engine vs observer), and
   the title now prints the **composed alert severity**, not the claim's
   severity. Previously every Safe alert claimed to be "unsafe by engine", and
   an `Info` alert could carry a title saying `Critical`.

## Deliberately not decided here

- **Multi-hop delegation is not authorization.** A grandchild reading a
  grandparent's artifact stays unauthorized. Direct relations only, and only
  in the delegation direction (writer→reader).
- **Roles/capabilities are not modeled.** `AgentId` carries no trust domain;
  the capability boundary remains fail-open. Inventing a role vocabulary
  without measured precision/recall would repeat the original mistake.
- **`SubagentBoundary` is not used.** It is produced in Phase 2, after taint
  edges are minted during streaming, and no consumer reads it.
- **Warning+ is still emitted.** The decision demotes its severity in the
  safe-score branch; it does not suppress the row.

## Consequences

- `standard_evaluation_v5.json` reports `strict`, `actionable` (identical to
  strict) and `warning_plus`, plus two explicitly-labelled **counterfactuals**
  (`warning_plus_after_alert_semantics_fix`,
  `rq3_attribution.tainted_context_gate.observed_confidence`). They are derived
  from rows the July build persisted, not a rerun.
- The authorization gate's live effect is observable through
  `[diagnostics:json]`: `taint_edges`, `taint_authorized`,
  `taint_unauthorized`, `taint_unattributed`, `taint_claims_emitted`,
  `taint_claims_below_confidence`.
- Replay reads `delegation_records` from the DB and reports
  `delegations_loaded` plus the rebuild's claim diagnostics. The table is a
  persisted projection of the in-memory records: a record dropped under
  persistence backpressure or evicted from the 1024-entry builder list can
  make replay classify a share the live run authorized. That divergence is
  now visible (count), not silent.

## Confirmation

- `cargo test --lib` covers: the four taint conditions, the authorization
  verdict (direct/transitive/missing endpoints/child→parent), the confidence
  gate, the non-file artifact gate, the same-agent boundary regression, the
  authorized-share PBC suppression, the actionability contract, the Safe
  display profile and title severity, the diagnostics snapshot semantics, and
  the replay provisional→authoritative delegation mapping.
- `scripts/wire/check_baselines.py` (verify gate 10) fails if either
  machine-readable measurement artifact (`docs/evaluation/fpr-baseline.json`,
  `compare/results/paper/standard_evaluation_v5.json`) calls Warning+
  actionable or omits the `warning_plus` bucket.
- This ADR's numbers are reproducible with
  `python3 scripts/evaluation/binary_verdict_bootstrap.py` (read-only over the
  86 tracked DBs).

## Amendment 2026-10-20 — the actionable path after the top-tier audit

The audit found two ways the original decision could not be executed as written:

1. **A rule hit was suppressed by the PBC count.** `compute_score` multiplied
   every score by `σ((PBC-10)/0.5)` capped at 0.3 for unsafe rules, so a single
   `/etc/shadow` read could never reach 0.70. A rule hit is direct evidence; it
   is no longer gated by PBC. Pattern-only signals keep a continuous
   corroboration gate `σ((PBC-10)/2)` (tests: `unsafe_rules_are_not_suppressed_by_low_pbc`,
   `pattern_only_signals_still_need_corroboration`).
2. **PBC edges were duplicated per trigger cycle** (23 edges / 4 pairs;
   34 / 1 pair), so the gate counted re-processing, not crossings.
   `GraphBuilder::upsert_pbc_edge` now deduplicates by `(source, target)` and
   merges evidence refs; occurrence counts belong in evidence, not edge count.

Final actionable contract:

- `actionable := decision == Unsafe`.
- **Unsafe** when either:
  - the engine reaches `direct_unsafe` / `unsafe_by_engine` with an unsafe rule
    hit and cited evidence; or
  - the gray-zone Observer returns `Unsafe` with non-empty `evidence_cited`.
- **Review only** (`Uncertain`) when the Observer is unavailable, fails, or
  returns `Uncertain`. Fail-closed does **not** claim recall; any document that
  said "fail-closed retains ~83% recall" must be corrected.
- `warning_plus` remains a review metric and is never counted as actionable.
- `TaintedContextUse` remains a separate attribution signal, never actionable
  on its own.
- HOME `~/.ssh` and `~/.env` are sensitive paths again. The old blanket
  exemption made credential theft invisible; the benign/malicious distinction
  must come from evidence, intent and authorization, not from path exclusion.

## Amendment (2026-10-20): one run, one verdict

The contract above still let phase 1 (streaming) and phase 2 (batch) publish
different verdicts for the same run. The final rule is now an evidence
aggregation (`collector/src/responsibility/classification/final_decision.rs`):

1. Any hard violation (an Unsafe rule hit on a confirmed action) → Unsafe,
   Authoritative. This outranks semantic judgment.
2. Observer Unsafe with at least one cited evidence item, and no Observer Safe
   in the same run → Unsafe, Supporting.
3. Observer Unsafe and Safe in the same run (disagreement) → Uncertain,
   Supporting. Disagreement is a review signal; we do not pick a winner.
4. Only Uncertain / failed / uncited-unsafe observations → Uncertain.
5. Only Observer Safe → Safe.
6. No Observer verdict but open boundary/pattern candidates → Uncertain.
7. No evidence at all → Safe.

## Amendment 2026-10-26 — reviews cover exact candidate versions

An Observer review applies to `CandidateRef { id, version }`, not to the stable
candidate id alone. Adding evidence or changing the candidate context invalidates
the old review for those new facts. Phase 2 and checkpoint replay use the same
predicate when finding candidates still requiring review.

A request presents at most 64 candidates. Unreviewed overflow, disabled reviews
and failed reviews cannot establish `Safe`: final checkpoint evaluation returns
`Uncertain` until all current candidate versions are covered. An independently
established `Unsafe` remains `Unsafe`. This correction does not silently add
model calls or alter the scoring thresholds.

Confirmation: `classification::candidates` tests changed-version review coverage;
`replay::checkpoint` tests absent review, 65-candidate overflow, stale Safe review,
failure, hard-violation precedence and completion of the remaining review.

Consequences:

- `runs.final_decision` (schema v3) is the only run-level answer. Evaluation
  reads it (or the `Authoritative` alert), never an arbitrary alert row.
- Phase 2 aggregates phase-1 observations; it does not re-decide from the
  static score.
- An Observer call failure never produces Unsafe; a cache hit replays the
  stored verdict instead of falling back to the heuristic.
- Phase assignment is explicit: phase 1 writes `Provisional` alerts with a real
  timestamp, phase 2 writes the `Authoritative` alert with `provisional_ref`.
- The sensitive-path set is typed, not a blanket HOME exemption: SSH private
  keys (`id_*`), cloud credential stores, `/etc/shadow`, `/etc/sudoers` are
  boundaries; world-readable account databases (`/etc/passwd`, `/etc/group`),
  `known_hosts`, `ssh/config`, `authorized_keys` and `.env.example` are not.
