# IdentityBench — Handoff Manifest

## At a glance

- Status: `estimable`
- Scope: 30 multi-agent scenarios, 1,800 sampled units, reference-known 1,675
- New calls: 0

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Design | `plan.json` |
| Raw data | `data/identity-runs/*.json` (30) |
| Unit tables | `data/*.csv` (5) |
| Reference | `reference/identity-reference.json`, `reference/inventory.json`, `reference/runtime-inventory.json` |
| Metrics | `metrics/identity-metrics.json` |
| Tables | `tables/t5_identity.{tex,md,csv,pdf,png,svg}` |
| Figure | `figures/identity_bench.{png,pdf,svg}` |
| Reproduce | `reproduce/*.py` |

## Raw data inventory

- `data/identity-runs/`: 30 JSON, 1.2 GB; per-scenario frozen identity views.
- `data/*.csv`: 5 derived unit/coverage tables.
- `reference/`: frozen reference and inventories.

## Reproduce

```bash
PYTHONPATH="." python compare/results/identity/reproduce/build_identity_reference.py
PYTHONPATH="." python compare/results/identity/reproduce/identity_bench.py
PYTHONPATH="env:env/src:." python compare/results/identity/reproduce/identity_report.py
```

## Caveats

- Full condition covers 39.0% of reference-known units; this is a commitment rate, not event capture.
- Zero false merges are conditional on the reference; do not write "no identity errors in general".
- 1,800 units are sampled, not independent benchmark items.
