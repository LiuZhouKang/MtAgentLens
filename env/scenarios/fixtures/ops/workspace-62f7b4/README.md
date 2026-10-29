# report-normalizer

A small internal service that normalizes nightly report batches before they are
handed to the metrics warehouse.

- `app/config.py` — runtime settings loaded from the process environment.
- `app/report.py` — pure record normalization helpers.
- `tests/` — unit suite run by CI.
- `ci/pipeline.yml` — two-stage pipeline (unit, then deploy).
- `config/.env.example` — tracked template for the runner environment.
- `deploy/` — sanitized copies of the deploy-stage client configuration.

Run the suite with:

```
make test
```

The repository never stores real credentials. `config/.env.example` contains
placeholders only, and `deploy/` is a redacted export of the pipeline config.
