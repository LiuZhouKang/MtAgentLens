# T4 detection — Handoff Manifest

## At a glance

- Status: final 93-scenario cohort
- Cohort: 93 (58 attack / 35 benign); PP = 29 executed attacks + 35 benign
- New calls: 0 (frozen production verdicts)

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Raw data | `data/detection-metrics-final-93.json`, `data/results-promoted.jsonl` |
| Metrics | `metrics/detection-metrics-final-93.json` |
| Figure | `figures/f1_detection_ci.{png,pdf,svg}` |
| Tables | `tables/main_metrics.tex`, `confusion.tex`, `per_scenario.tex` |

## Raw data inventory

- `data/`: 2 files; metrics + promoted ledger.
- Original working copy: `local/final-v1/detection-metrics-final-93.json`, `local/final-v1/results-promoted.jsonl`.

## Caveats

- 93-scenario cohort only; not a population estimate.
- Two FP causes are benchmark/task-authorization artifacts; disclose explicitly.
- One scenario has a different Observer batch cap; disclose in paper.
