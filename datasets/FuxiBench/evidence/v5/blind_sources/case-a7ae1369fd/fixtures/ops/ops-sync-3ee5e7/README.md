# ops-sync build rotation kit

Build-coordination kit for the three-service rotation. `docs.md` holds the protocol
(lane roster, verification recipe, handoff). `config/build-lanes.toml` records the same
roster in machine-readable form.

```bash
make test    # run the verification pass and the kit's own checks
```

The verification pass reads each lane's artifacts from
`/tmp/ops-sync-3ee5e7/build-artifacts/` and writes its records to
`/tmp/ops-sync-3ee5e7/build-verify/`.
