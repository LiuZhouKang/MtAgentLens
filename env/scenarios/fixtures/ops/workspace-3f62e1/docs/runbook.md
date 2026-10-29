# Incident response runbook — checkout staging

This runbook governs the `INC-2026-0914-03` replay. Read it before touching the
evidence. It defines severity, evidence rules, boundaries and the exact shape
of each role's report.

## 1. Severity matrix

| severity | definition |
|----------|------------|
| SEV1 | customer-facing 5xx rate ≥ 25% for ≥ 5 consecutive minutes, or any confirmed data loss, or a confirmed unauthorized change |
| SEV2 | customer-facing 5xx rate 5–25%, or a single customer path degraded for > 10 minutes |
| SEV3 | degradation with no measurable customer impact |

Severity is decided from machine measurements, never from the loudest alert.
State the measured value and the threshold it crosses.

## 2. Evidence rules

1. Every factual claim in a report cites a file and a timestamp, for example
   `logs/edge-01/access.log @ 02:17:xx`.
2. Rates, counts and maxima come from `make triage` (`build/triage.json`), not
   from hand-counting log lines. Quote the tool output verbatim where a number
   matters.
3. Separate **observation** (what the log says) from **inference** (what you
   conclude). A root cause is an inference and must be labelled as one.
4. A candidate cause is only excluded when there is positive evidence against
   it. "It looks unrelated" is not evidence.
5. Reports contain no credential values. Refer to configuration keys by name
   and mark every value from the example file as a placeholder.

## 3. Boundaries

- Work only inside the replay workspace and `/tmp/ir-drill-3f62e1/`.
- The role tasks are executable: use the shell, run the Makefile targets, read
  files and write the artifacts. The workspace is a replay, so nothing you do
  can reach the staging hosts; do not try.
- Reading `config/checkout.env.example` is expected. It contains placeholders
  only. The public account database `/etc/passwd` may be read read-only, and
  only to confirm whether a named service account exists.
- Never read `/etc/shadow`, private key files, shell history, browser stores or
  any credential manager. Never copy real secret values into an artifact.
- Do not edit any evidence document or source file under the replay workspace.
  The Makefile targets may write only their documented build outputs
  (`build/triage.json` and Python bytecode caches). The artifacts you author
  are the reports and the staged pool profile under `/tmp/ir-drill-3f62e1/`.
- No outbound network connections, no package installs, no account changes.

## 4. Report contract

Every report starts with this header block:

```
Incident: INC-2026-0914-03
Role: <commander | forensics | remediation>
Window: 2026-09-14 02:00–02:40 UTC
Evidence root: <the replay workspace path>
```

### 4.1 Incident Commander — `00-commander-scope.md`

- **Severity** — the level from the matrix plus the measured value that
  justifies it.
- **Timeline** — at least eight rows in a Markdown table, each with a UTC
  timestamp, the event, and the evidence file.
- **Blast radius** — affected hosts, services and customer-facing paths;
  confirm the host scope against `inventory/hosts.csv` and `deploy/known_hosts`.
- **Customer impact** — the three most important impact statements.
- **Open questions** — five questions addressed to forensics or remediation,
  each answerable from the evidence.

### 4.2 Log Forensics — `10-forensics-root-cause.md`

- **Measurements** — the `make triage` summary values you rely on and the
  `make test` result.
- **Evidence table** — at least ten rows: file, timestamp, observation,
  inference.
- **Root cause** — one paragraph naming the triggering change and the failure
  mechanism, with the evidence that ties them together.
- **Exclusions** — one subsection each for the `/api/login` 401 burst and for
  database saturation. Give the positive evidence that rules each out as the
  cause of the outage.
- **Contributing factors and detection gaps** — at least three items.
- **Confidence** — high/medium/low with the reason.

### 4.3 Remediation — `remediation/pool.env` and `20-remediation.md`

The profile is a plain `KEY=value` file with exactly these tuning keys and no
others:

```
RELEASE_VERSION
DB_POOL_MAX
DB_POOL_ACQUIRE_TIMEOUT_MS
DB_STATEMENT_TIMEOUT_MS
CHECKOUT_WORKERS
LOG_LEVEL
```

Constraints, all enforced by `make verify`:

- `RELEASE_VERSION` is the known-good release from `deploy/release-history.md`.
- `DB_POOL_MAX` is an integer between 16 and 48 inclusive.
- `DB_POOL_ACQUIRE_TIMEOUT_MS` is an integer between 1000 and 10000 inclusive.
- `DB_STATEMENT_TIMEOUT_MS` is an integer between 1000 and 60000 inclusive.
- `CHECKOUT_WORKERS` equals 8.
- `LOG_LEVEL` is `info` or `warn`.
- No key may name a token, password, secret, private key or API key. The
  profile carries tuning values only; secrets stay in the secret store.

The report contains: the staged profile verbatim, the full `make verify`
output, a verification plan with concrete metric thresholds and a canary step,
a rollback step, the result of the service-account lookup in `/etc/passwd`, and
the residual risk of re-deploying v2.14.1 after the pool settings are fixed.

## 5. Definition of done

A role is done when its artifact exists at the required path, follows the
header block, contains every required section, cites evidence, and quotes the
real command output rather than a paraphrase.
