# T7 PBC — distribution and activity baseline

> 93 scenarios; detector-derived PBC + raw activity baseline; **0 new paid calls**.

## What this shows

- PBC volume alone does not separate benign multi-agent activity: raw activity baseline at threshold 10 gives TPR 0.966 / FPR 0.686; threshold 20 gives TPR 0.931 / FPR 0.629.
- The detector's FPR is 0.057, much lower; PBC is supplementary, not a standalone detector.

## Files

- Raw: `data/pbc-distribution.json`, `data/STRATIFIED-METRICS.{json,md}`; see [`data/README.md`](data/README.md).
- Metrics: `metrics/pbc-metrics.json`.
- Figure: `figures/f2_pbc_strip.{png,pdf,svg}`.
- Table: `tables/STRATIFIED-METRICS.md`.

## Handoff

- [`MANIFEST.md`](MANIFEST.md)
- Narrative/evidence: [`docs/evaluation/e2-pbc-activity-baseline.md`](../../docs/evaluation/e2-pbc-activity-baseline.md)
