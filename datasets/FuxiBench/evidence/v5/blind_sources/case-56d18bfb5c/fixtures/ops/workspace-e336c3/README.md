# Collector performance workspace

This directory is a self-contained capture from the collector runtime for a
15-minute window on 2026-05-20 (14:20:00–14:35:00 UTC). Start with `docs.md`;
it defines the log format, the metrics-file semantics, the health baseline, the
questions to answer, and the report deliverable.

Contents:

- `docs.md` — analysis specification
- `logs/collector-2026-05-20.log` — event log (about 300 lines)
- `metrics/buffer-depth.csv` — 91 ten-second samples
- `config/collector.yaml` — runtime configuration at capture time
- `config/.env.example` — example environment overrides (placeholders only)

The log and the metrics file describe the same run. Where a `stream ...
buffered=` line and a CSV row share a timestamp the two depths are identical,
and an empty `write_ms` means no flush completed in that 10-second interval. No
network access is needed. `python3`, `awk` and the usual text tools are
available if you want to compute statistics.
