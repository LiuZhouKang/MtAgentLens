# IdentityBench — Handoff Manifest

## At a glance

- Status: `human_reviewed_estimable`
- Scope: 30 multi-agent scenarios; 1,800 sampled units; 1,675 reference-known
- Human annotators: 刘周康、唐浩钺、刘锦样
- New calls: 0

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Design | `plan.json` |
| Frozen identity views | `data/identity-runs/*.json` (30) |
| Unit tables | `data/*.csv` (5) |
| Human-reviewed reference | `datasets/FuxiBench/annotations/identity-bench/reference.json` |
| Metrics | `metrics/identity-metrics.json` |
| Tables | `tables/t5_identity.{tex,md,csv,pdf,png,svg}` |
| Figures | `figures/identity_bench.{png,pdf,svg}` |
| Reproduce | `reproduce/identity_bench.py`, `identity_audit.py`, `identity_report.py` |

## Reproduce

```bash
PYTHONPATH="." python compare/results/identity/reproduce/identity_bench.py
PYTHONPATH="env:env/src:." python compare/results/identity/reproduce/identity_audit.py
PYTHONPATH="env:env/src:." python compare/results/identity/reproduce/identity_report.py
```

## Interpretation

- Coverage and partition consistency use the frozen human-reviewed reference.
- Unknown units remain in the dataset and are not silently assigned.
- Results describe this 30-scenario cohort; they are not semantic Agent-role accuracy.
