# syncctl · sync verification toolkit

Small local toolkit used by the sync-platform working group to verify one sync
rollout and to file its chain record.

- `src/syncctl/chain.py` — reads and appends the line-oriented chain record.
- `src/syncctl/reconcile.py` — compares declared and observed hop counts.
- `src/syncctl/attest.py` — writes the attestation record for a verification run.
- `tests/` — verification suite; importing the package records one attestation.

```bash
make test   # run the verification suite
make lint   # compile the package
```

The status service the target reports to is configured in `config/sync.env`.
