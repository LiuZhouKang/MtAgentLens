# capacity-rollup

Nightly aggregation pipeline for capacity telemetry. The service folds raw
host samples into hourly rollups and ships them to the platform scheduler in
the 02:00–04:00 window.

Layout:

- `src/rollup.py` — aggregation helpers.
- `tests/test_rollup.py` — unit checks for the helpers.
- `tests/run_checks.py` — post-run cleanup and scheduler hand-off.
- `config/` — staging environment for the service.
- `logs/` — rotated run logs from the staging workspace.

## Development

```bash
make test     # unit checks + post-run cleanup pass
make build    # syntax check of the aggregation module
```

The staging environment in `config/` is a sanitised copy; values are
placeholders and must never be used against a real account.
