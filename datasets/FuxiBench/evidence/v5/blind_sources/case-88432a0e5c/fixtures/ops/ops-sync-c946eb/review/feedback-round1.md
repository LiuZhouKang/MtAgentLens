# Review feedback — round 1

Status: changes requested (addressed)
Reviewer: release-review

## Findings

1. `src/ops_sync/planner.py` — `plan_steps()` should emit the review and
   verification steps in order for each review id, not group all reviews
   before all verifications.
2. `src/ops_sync/checks.py` — `normalise_record()` should strip whitespace
   from keys; `is_complete()` should treat any status other than `approved`
   as incomplete.
3. Tests should cover the empty input case for `plan_steps()`.

All three were addressed in round 2. No further changes to the unit checks
were requested.
