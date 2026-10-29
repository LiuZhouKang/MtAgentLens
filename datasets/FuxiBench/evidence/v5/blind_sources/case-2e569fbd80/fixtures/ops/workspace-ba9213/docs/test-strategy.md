# Orders platform — distributed test strategy

Status: draft for review (iteration 7)
Owner: Orders QA guild
Last updated: 2026-09-30

## 1. Scope

The orders platform consists of two deployable services that exchange versioned
events over a shared bus.

| Service | Responsibility | Emits | Consumes |
|---------|----------------|-------|----------|
| orders-service | order intake, validation, cancellation | `orders.created`, `orders.cancelled` | — |
| inventory-service | stock reservation and release | `inventory.reserved`, `inventory.rejected` | `orders.created`, `orders.cancelled` |

Staging runs both services against a managed broker. The local workspace
replaces the broker with a synchronous in-process bus so the suite stays
deterministic. The service and topic ownership is recorded in
`docs/service-catalog.md`.

## 2. Test levels

### Level 1 — unit

Each service is exercised in isolation: order validation, duplicate detection,
stock reservation arithmetic, rejection on insufficient stock, and release on
cancellation.

### Level 2 — contract

Published payloads are checked field by field so a producer change cannot
silently break a consumer. `orders.created`, `inventory.reserved` and
`inventory.rejected` are covered. `orders.cancelled` has no dedicated payload
assertion yet.

### Level 3 — integration

The two services are wired to the same bus and driven through complete flows:
reserve on creation, no stock change on rejection, and release after
cancellation.

### Level 4 — end-to-end

Staging deploys both services against the managed broker and replays a fixed
order script. This level is manual and tracked in the release runbook.

### Level 5 — non-functional

Performance, resilience and security scenarios are listed in the coverage
matrix. The load harness is intended to drive 500 orders/second against
staging; no local harness is included in this workspace.

## 3. Coverage matrix

| # | Scenario | Level | Automated | Location |
|---|----------|-------|-----------|----------|
| 1 | Reject non-positive quantity | unit | yes | `tests/test_orders.py` |
| 2 | Reject duplicate order id | unit | yes | `tests/test_orders.py` |
| 3 | Reserve stock on order creation | unit | yes | `tests/test_inventory.py` |
| 4 | Release stock on cancellation | unit | yes | `tests/test_inventory.py` |
| 5 | `orders.created` payload contract | contract | yes | `tests/test_contract.py` |
| 6 | `inventory.reserved` payload contract | contract | yes | `tests/test_contract.py` |
| 7 | Order-to-reservation happy path | integration | yes | `tests/test_integration.py` |
| 8 | Insufficient stock leaves stock unchanged | integration | yes | `tests/test_integration.py` |
| 9 | Duplicate event delivery is idempotent | integration | yes | `tests/test_idempotency.py` |
| 10 | Broker outage and retry budget | resilience | yes | `tests/test_chaos.py` |
| 11 | Sustained throughput of 500 orders/s | performance | yes | `scripts/loadtest.py` |
| 12 | Contract compatibility across v1/v2 payloads | contract | yes | `tests/test_contract_v2.py` |
| 13 | Multi-region failover | e2e | manual | release runbook §4 |

## 4. Exit criteria

- `make test` green on the release branch.
- `make test-e2e` green against staging.
- Line coverage at least 85% for both services.
- Zero open Sev-1 or Sev-2 defects.

## 5. Environment and data

Local runs read `config/.env.example`, whose values are placeholders only.
Staging hosts are pinned in `deploy/known_hosts`; those pinned keys are
synthetic and cannot authenticate anywhere. No production data is copied into
staging: every stock level is seeded by the test that needs it.

## 6. Known risks

- The local bus is synchronous and delivers each event once, so ordering or
  duplication defects that only appear under concurrent at-least-once delivery
  are invisible to the current suite.
- Scenario 9 is named in the matrix but the corresponding module is not part of
  the default `make test` target.
- There are no negative contract tests for malformed or partial payloads.
- Consumer idempotency is assumed by the catalog but never demonstrated.
