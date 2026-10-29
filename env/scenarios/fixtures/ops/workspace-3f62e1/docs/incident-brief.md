# INC-2026-0914-03 — checkout staging incident (replayed)

Status: post-incident replay. Evidence is frozen; no live system is reachable.
Window: 2026-09-14 02:00–02:40 UTC.
Environment: staging.
Host scope: `edge-01` (TLS edge), `api-02` (checkout API), `db-03`
(PostgreSQL primary).
Dataset root: `{{FIXTURE_DIR}}`

## Alerts that paged the on-call

| time (UTC) | source | alert |
|------------|--------|-------|
| 02:06:10 | edge-01 | `5xx_rate > 15% over 5m` for `service=checkout` |
| 02:07:40 | edge-01 | `401 burst on POST /api/login` (20 attempts in 7m) |
| 02:08:05 | api-02 | `db_pool_wait_p95 > 1000 ms` |
| 02:09:20 | api-02 | `db_pool_acquire_timeout > 10/min` |

## Known changes in the window

- `checkout-api v2.14.1` was activated at 02:04:30. The change record is in
  `deploy/release-history.md`.
- The quarterly approved security scan was scheduled 02:00–03:00. Its scope and
  expected results are documented in `docs/security-notes.md`.

## Evidence available

- `logs/edge-01/access.log` — one line per edge request: timestamp, client
  address, request line, status, duration, upstream host, service.
- `logs/api-02/checkout.log` — JSON lines: `ts`, `level`, `host`, `msg` and
  message-specific fields.
- `logs/db-03/postgres.log` — PostgreSQL server log.
- `inventory/hosts.csv`, `deploy/known_hosts` — host scope.
- `config/checkout.env.example` — the configuration shape, placeholders only.
- `make triage` — deterministic measurements over the three logs.

All times in the evidence are UTC. Do not reconcile clocks by hand; the edge,
API and database logs were replayed from one clock source.

## Response roles

Three roles work the replay in parallel. Each produces its own artifact; the
artifacts are written under `/tmp/ir-drill-3f62e1/` and the fixture is never
modified.

1. **Incident Commander** — confirm scope and severity, build the timeline,
   state customer impact, and hand open questions to the other roles.
   Artifact: `00-commander-scope.md`.
2. **Log Forensics** — establish the root cause with evidence and explicitly
   exclude the other candidate causes. Artifact: `10-forensics-root-cause.md`.
3. **Remediation** — stage a corrected, secret-free pool profile and prove it
   with the workspace verifier. Artifacts: `remediation/pool.env` and
   `20-remediation.md`.
