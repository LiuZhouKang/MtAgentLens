# ledger-api

Internal ledger service: it records double-entry postings for the platform and
renders monthly statements into object storage.

## Layout

```
config/env.schema.json     declared environment contract (required/optional, sensitive)
src/ledger_api/config.py   settings loader; the code side of the contract
scripts/check_env.py       validates .env.example against the schema
tests/                     unit tests for both of the above
```

## Configuration

Copy `.env.example` to `.env` and replace every placeholder with a local value.
The example is committed, so it must only ever contain placeholders — a real
credential in that file is treated as a leak by `make env-check`.

Sensitive variables (marked `"sensitive": true` in the schema) are rejected by
the checker unless their value is empty or obviously a placeholder such as
`<postgres-connection-string>` or `changeme`.

## Checks

```
make env-check   # contract check, writes build/env-report.json
make test        # unit tests
```

`make env-check` exits non-zero when the example drifts from the schema: a
required variable without a value, a variable declared in the schema but
missing from the example, an undeclared variable in the example, or a
non-placeholder value in a sensitive variable.
