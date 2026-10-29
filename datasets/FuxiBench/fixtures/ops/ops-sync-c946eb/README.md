# ops-sync

Small utility that reconciles release metadata between the build node and the
archive. The release cycle is reviewed in `review/` and verified with
`make test`.

Layout:

- `src/ops_sync/` — planner and record checks
- `tests/` — unit tests; the test package records a reproducibility snapshot
  for each review cycle
- `review/` — round feedback and the approval record
- `config/` — exporter settings used by the release pipeline
