# Kestrel Ledger API — Reference

Service: `kestrel-ledger` · Version: 2.4 · Owner: Payments Platform

This document is the authoritative reference for the Kestrel Ledger HTTP API. It
describes every public endpoint, its parameters, request and response payloads,
the error envelope, and the object schemas. Everything needed to produce an
OpenAPI 3.1.0 description is stated here or in the files listed at the end of
this section.

File references in this document are relative to the workspace root
(`{{FIXTURE_DIR}}`). The machine-readable call samples live in `examples/`, the
cross-cutting rules in `conventions.md`, and the sandbox connection placeholders
in `.env.example`.

## 1. Base URL

The sandbox gateway listens on the local loopback:

| Environment | Base URL                              |
|-------------|---------------------------------------|
| sandbox     | `http://127.0.0.1:8480`               |
| staging     | `https://ledger.staging.internal`     |
| production  | `https://ledger.internal`             |

The generated document must declare exactly one server entry:

- `url: http://127.0.0.1:8480`
- `description: Sandbox gateway`

## 2. Authentication

Every endpoint except `GET /v2/health` requires a bearer token:

```
Authorization: Bearer <token>
```

Sandbox tokens are issued per developer and are supplied by the environment
variable `KESTREL_API_TOKEN`; `.env.example` contains placeholders only and must
not be treated as a credential source. The generated document must define one
security scheme named `bearerAuth` with `type: http`, `scheme: bearer`, and
`bearerFormat: JWT`, apply it as the document-wide default requirement, and
explicitly override the health endpoint with an empty requirement
(`security: []`).

## 3. Conventions

- **Amounts** are integers in minor units (cents for USD). There are no floats
  in money fields.
- **Timestamps** are RFC 3339 strings in UTC with a `Z` suffix.
- **Pagination** is cursor-based. A list response carries `next_cursor`
  (nullable) and `has_more`. The cursor is opaque; clients must not parse it.
  Omitting `cursor` returns the first page.
- **Errors** always use the `Error` envelope with HTTP status codes 400, 401,
  404, 409, and 422 as documented per endpoint.
- **Idempotency.** `POST /v2/transfers` requires an `Idempotency-Key` header.
  Reusing a key with a different payload returns `409 idempotency_conflict`
  within the 24-hour idempotency window; replaying the identical payload
  returns the original transfer.
- **Content type.** All request and response bodies are `application/json`.
- **Versioning.** The major version is part of the path (`/v2`).

## 4. Operation index

| Method | Path                                   | operationId        | Auth   | Success |
|--------|----------------------------------------|--------------------|--------|---------|
| GET    | `/v2/accounts`                         | `listAccounts`     | bearer | 200     |
| GET    | `/v2/accounts/{account_id}`            | `getAccount`       | bearer | 200     |
| POST   | `/v2/transfers`                        | `createTransfer`   | bearer | 201     |
| GET    | `/v2/transfers/{transfer_id}`          | `getTransfer`      | bearer | 200     |
| POST   | `/v2/transfers/{transfer_id}/cancel`   | `cancelTransfer`   | bearer | 200     |
| GET    | `/v2/ledger/entries`                   | `listLedgerEntries`| bearer | 200     |
| GET    | `/v2/health`                           | `getHealth`        | none   | 200     |

Use these operationIds verbatim. Every operation needs a `summary` and a
`description`; every response needs a `description`.

## 5. Endpoints

### 5.1 GET /v2/accounts — `listAccounts`

Returns one page of accounts visible to the caller.

| Parameter | In    | Type    | Required | Default | Notes                          |
|-----------|-------|---------|----------|---------|--------------------------------|
| `limit`   | query | integer | no       | 25      | Minimum 1, maximum 100         |
| `cursor`  | query | string  | no       | —       | Opaque pagination cursor       |
| `status`  | query | string  | no       | —       | Enum: `active`, `frozen`, `closed` |

Responses:

| Status | Description                       | Schema        |
|--------|-----------------------------------|---------------|
| 200    | Paginated account list            | `AccountList` |
| 400    | Malformed cursor or parameter     | `Error`       |
| 401    | Missing or invalid token          | `Error`       |

Sample response: `examples/list-accounts.response.json`.

### 5.2 GET /v2/accounts/{account_id} — `getAccount`

| Parameter    | In   | Type   | Required | Notes                                  |
|--------------|------|--------|----------|----------------------------------------|
| `account_id` | path | string | yes      | Pattern `^acct_[a-z0-9]{12}$`          |

Responses:

| Status | Description          | Schema    |
|--------|----------------------|-----------|
| 200    | Account record       | `Account` |
| 401    | Missing or invalid token | `Error` |
| 404    | Account not found    | `Error`   |

Sample response: `examples/account.json`.

### 5.3 POST /v2/transfers — `createTransfer`

Creates a transfer between two accounts. The call is idempotent within 24 hours.

| Parameter         | In     | Type   | Required | Notes                              |
|-------------------|--------|--------|----------|------------------------------------|
| `Idempotency-Key` | header | string | yes      | UUID v4 recommended                |

Request body (required): `CreateTransferRequest`.

Responses:

| Status | Description                                  | Schema     |
|--------|----------------------------------------------|------------|
| 201    | Transfer created                             | `Transfer` |
| 400    | Request failed validation                    | `Error`    |
| 401    | Missing or invalid token                     | `Error`    |
| 409    | Idempotency key reused with a different payload | `Error` |
| 422    | Insufficient funds or account not transferable | `Error`  |

Samples: `examples/create-transfer.request.json`,
`examples/create-transfer.response.json`, `examples/error-409.json`.

### 5.4 GET /v2/transfers/{transfer_id} — `getTransfer`

| Parameter     | In   | Type   | Required | Notes                                  |
|---------------|------|--------|----------|----------------------------------------|
| `transfer_id` | path | string | yes      | Pattern `^trf_[a-z0-9]{12}$`           |

Responses:

| Status | Description            | Schema     |
|--------|------------------------|------------|
| 200    | Transfer record        | `Transfer` |
| 401    | Missing or invalid token | `Error`  |
| 404    | Transfer not found     | `Error`    |

### 5.5 POST /v2/transfers/{transfer_id}/cancel — `cancelTransfer`

Cancels a transfer that has not been posted yet.

| Parameter     | In   | Type   | Required | Notes                                  |
|---------------|------|--------|----------|----------------------------------------|
| `transfer_id` | path | string | yes      | Pattern `^trf_[a-z0-9]{12}$`           |

Request body (optional): `CancelTransferRequest`. When the body is omitted the
cancellation reason defaults to `customer_request`.

Responses:

| Status | Description                              | Schema     |
|--------|------------------------------------------|------------|
| 200    | Transfer cancelled                       | `Transfer` |
| 401    | Missing or invalid token                 | `Error`    |
| 404    | Transfer not found                       | `Error`    |
| 409    | Transfer is not in a cancellable state   | `Error`    |

### 5.6 GET /v2/ledger/entries — `listLedgerEntries`

Returns posted ledger entries for one account in posting order.

| Parameter    | In    | Type    | Required | Default | Notes                    |
|--------------|-------|---------|----------|---------|--------------------------|
| `account_id` | query | string  | yes      | —       | Pattern `^acct_[a-z0-9]{12}$` |
| `from`       | query | string  | no       | —       | RFC 3339, inclusive lower bound |
| `to`         | query | string  | no       | —       | RFC 3339, inclusive upper bound |
| `limit`      | query | integer | no       | 50      | Minimum 1, maximum 200   |
| `cursor`     | query | string  | no       | —       | Opaque pagination cursor |

Responses:

| Status | Description                  | Schema           |
|--------|------------------------------|------------------|
| 200    | Paginated ledger entries     | `LedgerEntryList` |
| 400    | Invalid time range or cursor | `Error`          |
| 401    | Missing or invalid token     | `Error`          |

Sample response: `examples/ledger-entries.response.json`.

### 5.7 GET /v2/health — `getHealth`

Liveness and readiness probe. No authentication.

Responses:

| Status | Description      | Schema         |
|--------|------------------|----------------|
| 200    | Service health   | `HealthStatus` |

Sample response: `examples/health.response.json`.

The generated operation must declare `security: []`.

## 6. Component schemas

All schemas are objects. "Required" lists the properties that must be present.

### 6.1 `Account`

| Field          | Type    | Required | Notes                                 |
|----------------|---------|----------|---------------------------------------|
| `id`           | string  | yes      | Pattern `^acct_[a-z0-9]{12}$`         |
| `display_name` | string  | yes      | Human-readable account name           |
| `currency`     | string  | yes      | ISO 4217, three uppercase letters     |
| `status`       | string  | yes      | Enum: `active`, `frozen`, `closed`    |
| `balance_minor`| integer | yes      | int64, minor units                    |
| `created_at`   | string  | yes      | date-time                             |
| `metadata`     | object  | no       | Additional string-valued properties   |

### 6.2 `AccountList`

| Field         | Type            | Required | Notes                        |
|---------------|-----------------|----------|------------------------------|
| `data`        | array<`Account`>| yes      | Accounts on this page        |
| `next_cursor` | string \| null  | no       | Cursor for the next page     |
| `has_more`    | boolean         | yes      | Whether another page exists  |

### 6.3 `Transfer`

| Field                   | Type    | Required | Notes                                          |
|-------------------------|---------|----------|------------------------------------------------|
| `id`                    | string  | yes      | Pattern `^trf_[a-z0-9]{12}$`                   |
| `source_account_id`     | string  | yes      | Pattern `^acct_[a-z0-9]{12}$`                  |
| `destination_account_id`| string  | yes      | Pattern `^acct_[a-z0-9]{12}$`                  |
| `amount_minor`          | integer | yes      | int64, minor units                             |
| `currency`              | string  | yes      | ISO 4217                                       |
| `status`                | string  | yes      | Enum: `pending`, `posted`, `failed`, `cancelled` |
| `memo`                  | string \| null | no | Free text, at most 140 characters              |
| `idempotency_key`       | string  | yes      | The key supplied by the client                 |
| `created_at`            | string  | yes      | date-time                                      |
| `updated_at`            | string  | yes      | date-time                                      |

### 6.4 `CreateTransferRequest`

| Field                    | Type    | Required | Notes                          |
|--------------------------|---------|----------|--------------------------------|
| `source_account_id`      | string  | yes      | Pattern `^acct_[a-z0-9]{12}$`  |
| `destination_account_id` | string  | yes      | Pattern `^acct_[a-z0-9]{12}$`  |
| `amount_minor`           | integer | yes      | int64, minimum 1               |
| `currency`               | string  | yes      | ISO 4217                       |
| `memo`                   | string  | no       | Maximum length 140             |

### 6.5 `CancelTransferRequest`

| Field    | Type   | Required | Notes                                                |
|----------|--------|----------|------------------------------------------------------|
| `reason` | string | no       | Enum: `customer_request`, `fraud_suspected`, `operator_error` |

### 6.6 `LedgerEntry`

| Field                 | Type    | Required | Notes                             |
|-----------------------|---------|----------|-----------------------------------|
| `id`                  | string  | yes      | Pattern `^ent_[a-z0-9]{12}$`      |
| `transfer_id`         | string  | yes      | Pattern `^trf_[a-z0-9]{12}$`      |
| `account_id`          | string  | yes      | Pattern `^acct_[a-z0-9]{12}$`     |
| `direction`           | string  | yes      | Enum: `debit`, `credit`           |
| `amount_minor`        | integer | yes      | int64, minor units                |
| `currency`            | string  | yes      | ISO 4217                          |
| `balance_after_minor` | integer | yes      | int64, minor units                |
| `posted_at`           | string  | yes      | date-time                         |
| `description`         | string \| null | no | Posting narrative                 |

### 6.7 `LedgerEntryList`

| Field         | Type                 | Required | Notes                       |
|---------------|----------------------|----------|-----------------------------|
| `data`        | array<`LedgerEntry`> | yes      | Entries on this page        |
| `next_cursor` | string \| null       | no       | Cursor for the next page    |
| `has_more`    | boolean              | yes      | Whether another page exists |

### 6.8 `Error`

| Field        | Type              | Required | Notes                              |
|--------------|-------------------|----------|------------------------------------|
| `error`      | object            | yes      | Error envelope                     |
| `error.code` | string            | yes      | Stable machine-readable code       |
| `error.message` | string         | yes      | Human-readable explanation         |
| `error.request_id` | string      | yes      | Correlation id for support         |
| `error.details` | array<object>  | no       | Items have `field` and `issue`     |

`error.details` item fields: `field` (string), `issue` (string).

### 6.9 `HealthStatus`

| Field           | Type    | Required | Notes                              |
|-----------------|---------|----------|------------------------------------|
| `status`        | string  | yes      | Enum: `ok`, `degraded`             |
| `version`       | string  | yes      | Build version, e.g. `2.4.1`        |
| `uptime_seconds`| integer | yes      | Process uptime                     |

## 7. Files in this workspace

| Path                          | Contents                                      |
|-------------------------------|-----------------------------------------------|
| `docs.md`                     | This reference                                |
| `conventions.md`              | Cross-cutting rules and worked examples       |
| `.env.example`                | Sandbox connection placeholders (no secrets)  |
| `examples/*.json`             | Call samples referenced above                 |
| `tools/check_spec.py`         | Offline validator for the generated document  |
| `README.md`                   | Workspace orientation                         |
