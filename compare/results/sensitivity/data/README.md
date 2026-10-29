# T7-S sensitivity raw data

## `raw/`

- `raw/exports/`: 93 T7 ablation exports (same schema as `compare/results/ablation/data/exports`); 65 MB.
- `raw/detection-metrics-final-93.json`: frozen production verdict metrics for the 93 scenarios.
- `raw/results-promoted.jsonl`: promoted per-run ledger.
- `raw/families.json`: scenario-family split used for descriptive grouping.

These are the **inputs** from which `data/t7-sensitivity.json` is computed.

## `t7-sensitivity.json`

Derived result envelope:

- `surface`: 8103 exact threshold points (safe 111 × unsafe 73);
- `oat`: one-at-a-time weight/gate results;
- `lhs.samples`: 1000 joint Latin-hypercube draws;
- `dev_test`: descriptive split only.

Do not edit `t7-sensitivity.json` by hand; regenerate with `reproduce/build_sensitivity.py`.
