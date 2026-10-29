# External baselines — Handoff Manifest

## At a glance

- Status: comparator (T4 / review 3a, 8)
- Scope: 93 scenarios, PP = 29 executed attacks + 35 benign
- Baseline inputs: task, task+fixture, full app trace

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `baselines/README.md` |
| Design | `baselines/plan.json` |
| Raw data | `data/baseline-comparison-93.json`, `data/InjecAgent/*.json` |
| Metrics | `baselines/metrics/comparison.json`, `baselines/metrics/app-metrics.json` |
| Tables | `baselines/tables/table4_external_baselines.{tex,md}` |
| Figure | `baselines/figures/fig4_baseline_comparison.{png,pdf,svg}` |
| Reproduce | `baselines/reproduce/compute_metrics.py`, `baselines/reproduce/render_assets.py` |

## Raw data inventory

- `data/baseline-comparison-93.json`: aggregated external comparison.
- `data/InjecAgent/`: 4 frozen InjecAgent outputs.
- `baselines/`: manuscript-facing README/metrics/tables/figure.

## Caveats

- Inputs differ by baseline (task / task+fixture / full app trace); do not describe as identical-input superiority.
- R-Judge A2 only; no unverified extra baselines.
