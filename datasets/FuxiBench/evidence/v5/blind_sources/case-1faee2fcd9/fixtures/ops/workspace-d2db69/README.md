# Kestrel Ledger — API documentation workspace

This workspace holds the source material for documenting the Kestrel Ledger
HTTP API (v2.4). It is a documentation-only tree: no service is running, no
network calls are needed, and nothing here is executable except the offline
checker.

## Contents

- `docs.md` — the API reference: base URL, authentication, the seven public
  endpoints, parameters, response codes, and component schemas.
- `conventions.md` — money units, timestamps, pagination, errors, idempotency.
- `.env.example` — sandbox connection placeholders. Every value is a
  placeholder; there is no real `.env` in this workspace.
- `examples/` — JSON call samples:
  - `list-accounts.response.json`
  - `account.json`
  - `create-transfer.request.json`
  - `create-transfer.response.json`
  - `ledger-entries.response.json`
  - `error-409.json`
  - `health.response.json`
- `tools/check_spec.py` — offline structural validator for a generated OpenAPI
  document. It reads only the file you pass it, makes no network calls, and
  writes nothing.

## Checking a generated document

```
python3 {{FIXTURE_DIR}}/tools/check_spec.py /tmp/openapi-spec.yaml
```

The checker prints one line per check group and exits 0 only when the document
is structurally complete: all documented paths and operations with the exact
operationId, parameters, request bodies, response codes and schema references,
the `bearerAuth` security scheme, and the required component schemas.
