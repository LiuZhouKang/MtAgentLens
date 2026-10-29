# T8 overhead data

## `raw/`

- 2,952 files, 1.1 GB; per-run collector traces, samples and target outputs under:
  - `raw/steady/r1..r3/`
  - `raw/full-system/`
  - `raw/probes/`
- These are the original measurement files; analyze them directly or regenerate CSVs with `reproduce/audit_existing.py`.

## CSV/JSON derivatives

- `complete_system_summary.csv`, `full_system_runs.csv`, `full_system_samples.csv`
- `steady_runs.csv`, `steady_cpu_windows.csv`, `steady_process_samples.csv`, `steady_samples.csv`
- `controlled_operations.csv`, `credential_linkage.csv`, `credential_linkage.json`
- `numbers.json`, `figure_inputs_sha256.json`, `migration.csv`, `original_files.csv`

`archives/` contains superseded/attempt data; not for current citation.
