# reference-latency-suite

Connectivity-baseline harness for the reference endpoint set. The operator
procedure and report format live in `docs.md`; the target list lives in
`endpoints.txt`.

```bash
make sync    # run the three-round baseline sweep and emit baseline-report.json
make test    # validate the endpoint list and runbook wiring
```

The sweep is bounded by design: three rounds per endpoint, a two-second budget
per probe, and a twenty-second global deadline, so it is safe to run from CI.
