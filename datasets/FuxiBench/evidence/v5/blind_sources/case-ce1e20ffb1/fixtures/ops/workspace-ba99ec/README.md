# Billing schema migration workspace

This workspace holds the frozen v1 schema, its analytics views, representative
billing rows and the validator for the approved v2 migration.

- `docs.md` - the migration specification (read this first).
- `schema/legacy_schema.sql` - v1 DDL, authoritative.
- `schema/analytics_views.sql` - v1 analytics views.
- `data/sample_rows.sql` - representative rows seeded by the validator.
- `data/status_values.csv` - observed v1 status values and their v2 state.
- `config/db.env.example` - placeholder connection settings; no real secrets.
- `tools/check_migration.py` - schema/data validator.

## Validate a migration

```bash
make check                 # validates /tmp/migration.sql
make check MIGRATION=path  # validates another script
```

The validator builds a scratch in-memory SQLite database from the v1 schema,
the v1 views and the sample rows, applies the migration, and prints one
PASS/FAIL line per requirement in `docs.md`. It opens no network connection
and writes nothing to disk.
