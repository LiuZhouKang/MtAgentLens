# T7 ablation raw data

## `exports/`

- 93 files, one per FuxiBench scenario; 65 MB.
- Schema: `mtagentlens.ablation-rebuild/v1`, raw fact staircase `raw-fact-staircase-v1`.
- Each file contains the frozen per-scenario facts and layer scores used by `metrics/t7-metrics.json`.
- File name = scenario id; do not rename.

## Source of truth

- frozen exports: these files;
- promoted ledger: `local/final-v1/results-promoted.jsonl`;
- production verdict: `local/final-v1/detection-metrics-final-93.json`.

The three source files above are mirrored in `sensitivity/data/raw/` for that experiment; the exports here are the canonical T7 raw data.
