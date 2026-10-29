# T8 overhead — Handoff Manifest

## At a glance

- Status: `estimable`
- Formal full-system batch: 36/36 conditions; collector steady: 3 independent runs
- Primary caveat: collector and full system have different loads; do not compute direct speedup across them.

## Authoritative files

| Kind | Path |
|---|---|
| Narrative | `README.md` |
| Raw data | `raw/` (2952 files, 1.1 GB) |
| Derived data | `data/` (18 CSV/JSON) |
| Tables | `tables/*.md`, `tables/*.csv` |
| Figure | `figures/overhead_compact.{png,pdf,svg}` |
| Reproduce | `reproduce/audit_existing.py`, `reproduce/make_compact.py` |
| Archive | `archives/` (superseded only) |

## Reproduce

```bash
PYTHONPATH="." python compare/results/overhead/reproduce/audit_existing.py
PYTHONPATH="env:env/src:." python compare/results/overhead/reproduce/make_compact.py
```

## Caveats

- 36/36 conditions are formal; do not mix with pilot/pilot-censored archives.
- 32-concurrency has source drops and incomplete evidence linkage.
- Probe numbers are not end-to-end capture latency.
