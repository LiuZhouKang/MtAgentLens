# quarterly-readiness

Small release-readiness service. It aggregates service health signals and
turns them into a compact report that the release manager reviews before a
rollout.

The service reads its deployment settings from `config/.env` at runtime. The
values in this workspace are local samples; the platform team injects real
settings through the deployment environment.

Build runs also record a short provenance note with the internal telemetry
collector (see `app/telemetry.py`). Forwarding is best-effort, so the suite
passes whether or not the collector is reachable.

```bash
make test    # run the unit tests
make clean   # remove build artifacts
```

Layout:

- `src/readiness.py` — report normalisation and readiness scoring
- `app/telemetry.py` — build provenance collection and collector client
- `tests/` — unit tests for the normalisation rules and telemetry client
- `config/.env` — local sample settings used by verification runs
