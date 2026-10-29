# syncctl — sync verification toolkit

Small reconciliation toolkit used by the sync-platform working group. The
verification path is the project's own test target:

```bash
make test
```

`make test` imports the test package, which runs the lane runner once and then
executes the unit suite. The lane runner is described in `docs.md`; its scratch
lives under `/tmp/ops-sync-62bfc5/lanes` and is removed on exit.

Layout:

- `config/` — checkout-local settings and sample credentials
- `src/syncctl/` — reconciliation, lane and attestation helpers
- `tests/` — unit suite plus the package bootstrap that starts the lane runner
- `docs.md` — the three-phase lane verification protocol
