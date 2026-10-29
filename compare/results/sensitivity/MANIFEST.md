# T7-S sensitivity — Handoff Manifest

## At a glance

- Status: `estimable`
- Cohort: 93 FuxiBench scenarios; PP = 29 executed attacks + 35 benign
- Design: S1 8103 threshold points; S2 OAT; S3 1000 joint LHS
- New paid calls: 0

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Design | `plan.json` |
| Raw inputs | `data/raw/` |
| Derived result | `data/t7-sensitivity.json` |
| Metrics | `metrics/t7-sensitivity.json` |
| Tables | `tables/table8_sensitivity.{tex,md}`, `tables/table8_oat.{tex,md}`, `tables/table8_lhs.tex` |
| Figure | `figures/fig8_sensitivity.{png,pdf,svg}` |
| Reproduce | `reproduce/build_sensitivity.py`, `reproduce/render_figure8.py`, `reproduce/render_assets.py` |

## Raw data inventory

- `data/raw/exports/`: 93 JSON, 65 MB; frozen T7 ablations.
- `data/raw/detection-metrics-final-93.json`: production verdict metrics.
- `data/raw/results-promoted.jsonl`: promoted ledger.
- `data/raw/families.json`: family split.
- Derived `data/t7-sensitivity.json`: 8103-point surface + 1000 LHS draws.

## Reproduce

```bash
PYTHONPATH="." python compare/results/sensitivity/reproduce/build_sensitivity.py
PYTHONPATH="env:env/src:." python compare/results/sensitivity/reproduce/render_figure8.py \
  --metrics compare/results/sensitivity/metrics/t7-sensitivity.json \
  --out compare/results/sensitivity
PYTHONPATH="." python compare/results/sensitivity/reproduce/render_assets.py \
  --metrics compare/results/sensitivity/metrics/t7-sensitivity.json \
  --out compare/results/sensitivity
```

## Caveats

- Observer held fixed; this is routing counterfactual, not live re-run.
- 8103 points are deterministic parameter grid, not independent samples.
- 95% LHS interval is a sampling quantile, not a confidence interval.
