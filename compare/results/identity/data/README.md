# IdentityBench raw data

## `identity-runs/`

- 30 JSON files, one per multi-agent scenario; 1.2 GB total.
- Schema: per-scenario frozen identity views used by the 1,800-unit sample.
- These are the largest raw files; keep them with the experiment, do not replace with summaries.

## Derived CSVs

| File | Meaning |
|---|---|
| `identity_units.csv` | 1,800 sampled units with assignment/ambiguity state |
| `kind_coverage.csv` | message/intent/action identity coverage |
| `pair_counts.csv` | reference-known pair counts |
| `reference_coverage.csv` | reference coverage by condition |
| `scenario_metrics.csv` | per-scenario metrics |

## Source

Frozen identity views; no new model calls or scenario reruns in this round.
