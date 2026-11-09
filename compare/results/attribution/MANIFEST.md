# T6-Root — Handoff Manifest

## At a glance

- Status: **root-only estimable**
- Cohort: 30 multi-agent scenarios; 22 attack, 8 benign
- Predictions: 22,460 frozen tracebacks from 93 run DBs
- Human annotators: 刘周康、唐浩钺、刘锦样
- New paid calls: 0

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Raw data | `data/` (93 JSON, 58 MB) |
| Root predictions | `predictions/*.json` (30) |
| Blinded packets | `packets/*.json` (30) |
| Human-reviewed reference | `datasets/FuxiBench/annotations/attribution-bench/root-reference.json` |
| Metrics | `metrics/t6-root.json` |
| Tables | `tables/t6_root.{md,tex}` |
| Figures | `figures/t6_root.{png,pdf,svg}` |
| Reproduce | `reproduce/build_root_predictions.py`, `evaluate_root_full.py`, `render_root.py` |

## Results

- Root@1 = 22.7% (5/22)
- Root@2 = 54.5% (12/22)
- Root@3 / candidate coverage = 100% (22/22)
- MRR = 0.538
- RootSet F1 = 0.500 (3 candidates)

## Caveats

- The reference has been reviewed and revised by the three listed human annotators.
- This result evaluates root candidates only; Entry and Chain remain unmeasured.
- Candidate set is fixed at three; Root@3 equals candidate coverage.
- Root@1 is 22.7%; do not claim root-cause localization is solved.
