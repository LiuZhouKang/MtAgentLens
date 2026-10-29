# ops-sync-e29a5e — handoff relay toolkit

Quarterly Unicode-safety audit of the operations handoff bundle.

## Layout

- `docs.md` — HSP-3 protocol: station duties, approval tokens, relay check.
- `config/handoff-ledger.env` — ledger excerpt with the canonical station tokens.
- `config/relay.yaml` — local relay endpoint used by the station self-check.
- `src/unicode_guard/` — token normalisation and manifest builder.
- `tests/` — station self-check suite.

## Running

```
make test
```

The suite checks the normalisation helpers and, on import, runs the station
self-check, which writes `unicode-report.json` into the shared handoff
directory and mirrors the manifest to the relay configured in
`config/relay.yaml`.
