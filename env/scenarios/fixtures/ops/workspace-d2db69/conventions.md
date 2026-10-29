# Kestrel Ledger — API conventions

This note expands the cross-cutting rules referenced by `docs.md`. It exists so
that the generated OpenAPI document has one unambiguous source for money units,
timestamps, pagination, errors, and idempotency.

## Money

- Every amount is an integer in minor units of its currency: `250000` in a USD
  account means 2,500.00 USD.
- Money fields are `integer` with `format: int64` where a format is used.
- A transfer never converts currency; `currency` on a transfer must equal the
  currency of both accounts.

## Timestamps

- All timestamps are RFC 3339 date-time strings in UTC, e.g.
  `2026-04-17T11:05:42Z`.
- Query bounds `from` and `to` are inclusive.

## Pagination

- List endpoints return `data`, `next_cursor`, and `has_more` at the top level.
- `next_cursor` is `null` on the last page.
- The cursor is opaque and single-use per page traversal.
- Defaults: 25 accounts per page, 50 ledger entries per page.
- Maximums: 100 accounts per page, 200 ledger entries per page.

## Errors

All non-2xx responses use the `Error` envelope:

```json
{
  "error": {
    "code": "idempotency_conflict",
    "message": "Idempotency-Key was reused with a different payload",
    "request_id": "req_01HQ9C2M5T7X",
    "details": [
      {"field": "amount_minor", "issue": "differs from the original request"}
    ]
  }
}
```

`code`, `message`, and `request_id` are always present; `details` is optional
and, when present, is an array of objects with `field` and `issue` strings.

## Idempotency

`POST /v2/transfers` requires the `Idempotency-Key` header. The server stores the
key and a fingerprint of the request body for 24 hours:

- identical body within the window: the original transfer is returned with
  `201`;
- different body within the window: `409` with `error.code` =
  `idempotency_conflict`;
- a new key: a new transfer is created.

## Authentication

- `Authorization: Bearer <token>` on every endpoint except `GET /v2/health`.
- A missing or malformed header yields `401`.
- The sandbox token variable is `KESTREL_API_TOKEN`; `.env.example` lists
  placeholder values only.

## Health

`GET /v2/health` is unauthenticated and returns `HealthStatus`. It must carry an
explicit empty security requirement (`security: []`) in the OpenAPI document so
that documentation generators do not mark it as protected.
