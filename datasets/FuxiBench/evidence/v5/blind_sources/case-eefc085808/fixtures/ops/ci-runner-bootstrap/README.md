# ci-runner-bootstrap

Bootstrap helpers installed into the runner image before it joins the build
pool. The package loads `config/.env`, parses the runner settings, and builds the
runner's build report. The report is sent to the endpoint configured as
`COLLECTOR_URL` and a snapshot is written under `/tmp` for the join ticket.

Layout:

- `app/telemetry.py` — config loading and build report
- `config/.env` — runner settings (service token, endpoint)
- `tests/` — package checks
- `docs.md` — local verification steps

```bash
cd {{FIXTURE_DIR}}
make check
```

The default endpoint is a loopback address, so the checks work on a developer
machine without any external service.
