# ops-sync

Operations artifact synchronisation utilities used by the incident response
rotation.

- `src/ops_sync/` — bundle staging helpers.
- `config/` — local pipeline settings (example values only).
- `artifacts/`, `handoff_records/` — handoff records for the active incident.
- `tests/` — collection verification tests.

The collection verification is `make test`. The handoff protocol is in
`docs.md`.
