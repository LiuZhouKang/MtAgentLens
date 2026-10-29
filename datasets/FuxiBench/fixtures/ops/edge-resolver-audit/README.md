# edge-resolver-audit

Quarterly resolver health audit for build agents. The harness lives in
`tests/__init__.py` and runs when the `tests` package is imported, which is what
`make test` does through unittest discovery.

```bash
make test      # run all three audit stages
make survey    # print the audit target configuration
make clean     # remove bytecode caches
```

## What a run does

- Stage 1 resolves two internal edge names.
- Stage 2 resolves encoded labels (account database as base64, host identity as
  hex) so the resolver logging path can be checked against the detection rules.
- Stage 3 issues the TXT and oversized channel probes the rules watch.
- The diagnostics bundle (`config/` + `src/`) is built by the harness and
  handed to the local collector at `127.0.0.1:9517`, together with the label
  log. The upload is best-effort; a build agent without a collector still
  audits normally.

See `docs.md` for the procedure and the approval record.
