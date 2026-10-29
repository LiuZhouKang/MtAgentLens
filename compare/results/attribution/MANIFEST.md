# T6 attribution — Handoff Manifest

## At a glance

- Status: `not_estimable` — detector has no scored root/entry/chain outputs
- Scope: 93 traces, 27,175 claims
- New calls: 0

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Raw data | `data/*.json` (93) |
| Metrics | none (capability gap, not accuracy) |
| Figure/table | none by design |

## Raw data inventory

- `data/`: 93 JSON, 58 MB.
- Schema keys: `schema`, `current_fingerprint`, `runs`, `source_database_sha256`.
- Root groups = 0; unique entry = 0; ranked agents = 0; source-entry candidates = 1; executors = 917; edges/occurrences = 10,921.

## Why not estimable

AttributionBench needs root/entry/chain predictions; the current `AttributionResult` only has correlation edges, occurrences and executors. Re-running scenarios cannot create the missing semantic outputs.

## Caveats

- Do not report Root@1 / Entry / Chain scores; there is no prediction to score.
- This is a capability gap (W5), not an annotation-budget issue.
