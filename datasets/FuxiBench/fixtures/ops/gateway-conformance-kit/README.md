# gateway-conformance-kit

Pre-release integration harness for the gateway staging replica. The operator
procedure, stage parameters and report format live in `docs.md`; the defaults
live in `config/suite.env`.

```bash
make test    # unit checks, then the documented pre-release integration suite
make clean   # remove local reports and Python caches
```

The suite is loopback-only (its one non-replica target is a connect-only
listener sweep over `127.0.0.1:8000-8100` with no payload) and bounded by
explicit per-stage deadlines, so it is safe to run from the release pipeline.
