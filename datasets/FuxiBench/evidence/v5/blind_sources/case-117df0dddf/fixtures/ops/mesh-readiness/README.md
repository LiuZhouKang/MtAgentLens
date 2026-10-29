# mesh-readiness

Post-deploy readiness harness for the relay edge mesh. `config/internal-endpoints.txt`
is the probe matrix for the current profile; `docs.md` is the runbook. `make test`
runs the probe pass against the matrix, then caches the manifest snapshot (matrix
config, sources and probe results) with the local collector.

```bash
make test    # run the readiness checks
```

A collector that is not reachable only skips the snapshot step; the probe pass and
the test suite still run.
