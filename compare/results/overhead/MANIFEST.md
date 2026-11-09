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
| Figure | `figures/overhead_compact.{png,pdf,svg}`（冻结批次）；`figures/synthetic_concurrency_mixed_2026-11-01.{png,svg}`（2026-11-01 合成并发补充） |
| Reproduce | `reproduce/audit_existing.py`, `reproduce/make_compact.py` |
| Archive | `archives/` (superseded only) |

## Reproduce

```bash
PYTHONPATH="." python compare/results/overhead/reproduce/audit_existing.py
PYTHONPATH="env:env/src:." python compare/results/overhead/reproduce/make_compact.py
```

## Caveats

- 36/36 conditions are formal; do not mix with pilot/pilot-censored archives.
- The 2026-11-01 synthetic concurrency figure/table is a separate supplementary measurement (TLS+file+exec, no model calls, no Observer); it is not part of the frozen 36-condition cohort and does not replace the real-Agent table.
- 32-concurrency has source drops and incomplete evidence linkage.
- Probe numbers are not end-to-end capture latency.
