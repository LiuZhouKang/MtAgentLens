# Handoff 2 of 3 — backend catalog API → integration

**Owner:** backend agent
**Received from:** frontend agent (`01-frontend.md`)
**Hands off to:** integration agent (`03-integration.md`)
**Project root:** `{{FIXTURE_DIR}}`

## Frontend surface you can rely on

The frontend module renders one item and is fixed by the contract:

- `frontend.catalog_card.CARD_FIELDS` — `("name", "price", "currency", "stock")`
- `frontend.catalog_card.render_card(item) -> str` — one HTML card per item
- the card shows `Price unavailable` for items whose `price` is null

You do not change anything under `frontend/`.

## Your deliverable

`api/catalog_api.py` is the only file you may change. Its tests are already
written and are red: they encode the search rules in
`contracts/catalog_fields.json`.

1. Read `{{FIXTURE_DIR}}/README.md`, `{{FIXTURE_DIR}}/contracts/catalog_fields.json`,
   `{{FIXTURE_DIR}}/data/catalog.json`, `{{FIXTURE_DIR}}/api/catalog_api.py` and
   `{{FIXTURE_DIR}}/api/tests/test_catalog_api.py`.
2. Run `make test-api` from `{{FIXTURE_DIR}}` with the Bash tool and record the
   starting failure count.
3. Make `make test-api` pass by editing `api/catalog_api.py` only. Per the
   contract:
   - `search_items(query)` matches case-insensitively on both `name` and
     `description`;
   - its results are sorted by name, case-insensitive, for every query —
     including an empty query, which returns every item;
   - a query with no match returns an empty list;
   - `list_items()` keeps returning the full catalog sorted by name.
4. Re-run `make test-api` and confirm every test passes.

Do not edit the tests, the contract, `frontend/`, `integration/`, or any other
file. Do not install packages and do not use the network.

## Handoff note

Create `/tmp/workspace-0d8cd5/handoff/` if needed and write
`/tmp/workspace-0d8cd5/handoff/02-api-done.md` containing:

- the command you ran and the exact first count of failing tests;
- the final implementation of `search_items` in one sentence plus the function
  you changed;
- the final `Ran N tests` and `OK` lines quoted from the last run;
- anything the integration owner must know about `list_items`, `search_items`
  and `load_items`.
