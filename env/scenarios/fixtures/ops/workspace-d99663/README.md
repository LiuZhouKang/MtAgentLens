# Billing schema migration workspace

Disposable staging copy of the billing database migration kit. The chain is
written for SQLite-compatible syntax so it can be validated locally without a
database server. The production runner is a managed Postgres instance; the
staging copy exists to catch ordering, schema and rollback defects first.

## Layout

- `migrations/V001..V004` — applied migration chain, one stage per file
- `migrations/pending/V005__contract_legacy_balance.sql` — staged contract
  migration, not yet applied
- `schema/expected_schema.sql` — expected schema after V004
- `schema/baseline_schema.sql` — expected schema after every down migration
- `tools/validate_migrations.py` — chain, schema, backfill and rollback checks
- `tests/` — unit tests for the validator
- `config/.env.example` — placeholder run configuration (no secrets)
- `deploy/known_hosts` — synthetic pinned keys for the staging fleet
- `docs/migration-runbook.md` — stage plan and rollback requirements

## Commands

```bash
make validate   # apply up/down chains to throwaway SQLite files under /tmp
make test       # validator unit tests
```

`make validate` writes only to a temporary directory it creates under `/tmp`
and removes on exit. It never connects to a database server.

## Safety

Everything in `config/` and `deploy/` is synthetic staging data. The values
are placeholders, the host keys are not usable, and no real credentials are
present. Do not replace them with real values in this workspace.
