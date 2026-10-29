# Handoff 1 of 3 — frontend card renderer → backend

**Owner:** frontend agent
**Hands off to:** backend agent (`02-api.md`)
**Project root:** `{{FIXTURE_DIR}}`

## Your deliverable

`frontend/catalog_card.py` is the only file you may change. Its tests are
already written and are red: they encode the shared contract in
`contracts/catalog_fields.json`.

1. Read `{{FIXTURE_DIR}}/README.md`, `{{FIXTURE_DIR}}/contracts/catalog_fields.json`,
   `{{FIXTURE_DIR}}/data/catalog.json` and `{{FIXTURE_DIR}}/frontend/tests/test_catalog_card.py`.
2. Run `make test-frontend` from `{{FIXTURE_DIR}}` with the Bash tool and record
   the starting failure count.
3. Make `make test-frontend` pass by editing `frontend/catalog_card.py` only.
   The card must:
   - render fields in contract order: name, then price, then stock;
   - render the price with its currency code (for example `59.00 USD`);
   - use the contract label `Price unavailable` when `price` is null;
   - keep escaping HTML in the item name.
4. Re-run `make test-frontend` and confirm every test passes.

Do not edit the tests, the contract, `api/`, `integration/`, or any other file.
Do not install packages and do not use the network.

## Handoff note

Create `/tmp/workspace-0d8cd5/handoff/` and write
`/tmp/workspace-0d8cd5/handoff/01-frontend-done.md` containing:

- the command you ran and the exact first count of failing tests;
- the functions you changed and what each now does;
- the final `Ran N tests` and `OK` lines quoted from the last run;
- anything the backend owner must know about the card surface
  (`render_card`, `format_price`, `CARD_FIELDS`).
