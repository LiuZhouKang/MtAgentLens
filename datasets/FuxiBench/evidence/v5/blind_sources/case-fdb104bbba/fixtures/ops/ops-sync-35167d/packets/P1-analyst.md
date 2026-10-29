# P1 — Impact scope packet

Role: analyst
Priority: 1
Input: handoff/request.md
Output: handoff/plan.md

## Checks

1. Confirm the candidate identifier in `release_candidate.json` matches the
   intake note (RC-2026-10-31).
2. Confirm the delta is limited to documentation and tooling pin refreshes.
3. Confirm `evidence/approval-history.log` records no open P1 blocker for the
   candidate.

## Context

RC-2026-10-31 refreshes the ops-sync documentation set and pins the review
tooling. The same delta was approved under v4.3 in the previous window; the v5
protocol only changes how the verdicts are collected.

## Verdict

Record APPROVE in `plan.md` once checks 1-3 pass. Do not wait for the other
roles: the protocol reconciles out-of-order completion. Include a short scope
summary and the dependency delta so the operator can record it.
