# T7 ablation — Handoff Manifest

## At a glance

- Status: `estimable` (two endpoints: ranking + operating point)
- Cohort: 93 FuxiBench scenarios (58 attack / 35 benign); PP = 29 executed attacks + 35 benign
- New paid calls: 0

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Design | `plan.json` |
| Raw data | `data/exports/*.json` (93) |
| Metrics | `metrics/t7-metrics.json` |
| Tables | `tables/table7_ranking.{tex,md}`, `tables/table7_operating.{tex,md}` |
| Figure | `figures/fig7_ablation.{png,pdf,svg}` |
| Reproduce | `reproduce/build_metrics.py`, `reproduce/render_assets.py` |

## Raw data inventory

- `data/exports/`: 93 JSON, 65 MB.
- Schema: `mtagentlens.ablation-rebuild/v1`; raw fact staircase `raw-fact-staircase-v1`.
- Each scenario file is self-contained; no external path is needed to inspect a run.

## Reproduce

```bash
PYTHONPATH="." python compare/results/ablation/reproduce/build_metrics.py
PYTHONPATH="env:env/src:." python compare/results/ablation/reproduce/render_assets.py \
  --metrics compare/results/ablation/metrics/t7-metrics.json \
  --out compare/results/ablation
```

## Caveats

- L2/L3 verdict state is partial; ranking endpoint does not depend on Observer.
- L3 is recorded production verdict, not a new live Observer replay.
- Graph AUROC +0.087 but 95% CI crosses 0; do not write "significant".
