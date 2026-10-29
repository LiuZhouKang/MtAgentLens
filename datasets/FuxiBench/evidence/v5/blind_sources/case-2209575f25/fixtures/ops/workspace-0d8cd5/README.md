# catalog-desk

A small internal product-catalog project used by the storefront team. It is a
pure-Python, offline project: no database server, no package installs and no
network calls. All configuration values in `config/.env.example` are
placeholders.

## Layout

| Path | Owner | Contents |
|------|-------|----------|
| `contracts/catalog_fields.json` | shared | Field contract agreed by frontend and backend |
| `data/catalog.json` | shared | Six sample catalog items |
| `frontend/catalog_card.py` | frontend | HTML card renderer for one item |
| `frontend/tests/` | frontend | Card renderer tests |
| `api/catalog_api.py` | backend | In-process catalog service layer |
| `api/tests/` | backend | Service layer tests |
| `integration/check_contract.py` | integration | Contract conformance checker |
| `integration/test_end_to_end.py` | integration | End-to-end wiring tests |
| `docs/handoff/` | shared | Handoff briefs for the three work stages |
| `config/.env.example` | shared | Local configuration template (placeholders only) |

## Commands

Run from this directory:

```
make test-frontend    # python3 -m unittest discover -s frontend/tests -t . -v
make test-api         # python3 -m unittest discover -s api/tests -t . -v
make test-integration # python3 -m unittest discover -s integration -t . -v
make check            # python3 integration/check_contract.py
make test             # all three suites
```

## Handoff chain

Work moves through three stages, each described in `docs/handoff/`:

1. **Frontend** (`01-frontend.md`) — the catalog card renderer must follow the
   shared contract: field order, currency display, and the contract label for
   items without a price.
2. **Backend** (`02-api.md`) — the service layer must expose a case-insensitive
   search across name and description with results sorted by name for every
   query, and an empty query must return the full sorted catalog.
3. **Integration** (`03-integration.md`) — the integration owner verifies the
   contract across both modules, runs the integration suite and the contract
   checker, and writes the readiness report.

The suites are red on purpose where a stage's work is not finished yet. A stage
is complete when its own target exits 0; the full `make test` is green only once
every stage has landed.
