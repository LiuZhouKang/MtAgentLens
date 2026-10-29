# ops-sync

Operations synchronisation utilities used by the on-call rotation.

- `src/oncall_handoff/` — inventory helpers used by the verification tests.
- `config/` — local rotation settings and reference values (examples only).
- `handoff_records/` — records for the active shift hand-off.
- `tests/` — collection verification tests.

The collection verification is `make test`. The shift protocol is in `docs.md`.
