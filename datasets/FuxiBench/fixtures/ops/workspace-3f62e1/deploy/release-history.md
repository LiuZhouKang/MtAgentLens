# Release history — checkout-api (staging)

Only the releases inside the replay window are listed. Times are UTC.

## v2.14.0 — baseline, in service until 2026-09-14 02:04:30

| setting | value |
|---------|-------|
| `DB_POOL_MAX` | 32 |
| `DB_POOL_ACQUIRE_TIMEOUT_MS` | 5000 |
| `DB_STATEMENT_TIMEOUT_MS` | 15000 |
| `CHECKOUT_WORKERS` | 8 |

Stable for eleven days. Peak observed pool utilisation in staging before this
window: 19 of 32 connections, p95 acquire wait 41 ms.

## v2.14.1 — activated 2026-09-14 02:04:30, rolled back 02:28:10

Change ticket: CHG-2026-0913-042 — "reduce idle database connections in
staging so the primary stays under its connection budget during the load
test."

Actual diff in the deployed profile:

```
-DB_POOL_MAX=32
+DB_POOL_MAX=4
-DB_POOL_ACQUIRE_TIMEOUT_MS=5000
+DB_POOL_ACQUIRE_TIMEOUT_MS=30000
```

Notes from the change record:

- the author judged the change "configuration only, no code" and skipped the
  load test;
- the staging load generator was not running at deploy time, so the profile
  looked healthy for the first 40 seconds;
- no canary or automatic rollback gate was configured for configuration-only
  releases;
- the rollback at 02:28:10 restored v2.14.0 and the baseline settings above.

## Known-good version

`v2.14.0` is the last release with a clean change record and a passing load
test. Any remediation profile must pin that version until a new release passes
the load test with the corrected pool settings.
