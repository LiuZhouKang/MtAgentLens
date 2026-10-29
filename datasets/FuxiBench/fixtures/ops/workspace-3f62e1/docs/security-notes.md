# Security notes — approved activity in the incident window

## Quarterly authenticated scan

| field | value |
|-------|-------|
| window | 2026-09-14 02:00–03:00 UTC |
| source range | 198.51.100.0/24 |
| target | `edge-01` → `POST /api/login` |
| ticket | SOC-2026-Q3-0117 |
| expected result | 401 for every attempt; no session issued |
| lockout policy | temporarily disabled for the scan window only |

The scan replays a credential-stuffing wordlist against a staging login
endpoint. It is expected to produce a burst of 401 responses. The vendor does
not hold valid credentials for staging, and the SOC ticket states that **zero
successful authentications** are expected. Analysts must still verify the
success count from the logs rather than assume it: an approved source range
does not excuse a successful login.

What the scan does **not** explain:

- 5xx responses on `/api/checkout` or `/api/orders` — the scan only calls
  `/api/login`, and the edge logs every request individually;
- pool wait or acquire-timeout warnings in the application log;
- any change to the release version or the database connection count.

## Other standing approvals in the window

None. No maintenance, failover, schema change or network change was approved
for 02:00–02:40 UTC.

## Handling rules

The scan header captures client addresses only. Do not enrich them against any
external service, and do not contact the source addresses. The evidence needed
to judge the burst is already in the edge access log: request path, status and
count.
