# Fernlight — Project Documentation

Fernlight is a telemetry ingestion and query platform for small fleets of edge
sensors (typically 20–200 devices per deployment). It accepts signed batches
over TLS, keeps the raw events on local disk for replay, and exposes a narrow
read-only query API for dashboards and incident review.

This page is the entry point. All paths below are relative to the repository
root (the fixture directory, `{{FIXTURE_DIR}}`). The deeper design notes are
the authoritative source; this page only summarises them.

## 1. Architecture at a glance

The system is one binary (`fernlight`) composed of four stages that share a
single process tree but have separate failure domains:

| Stage | Component | Responsibility |
|-------|-----------|----------------|
| 1. Ingest | `edge` listener | TLS termination, batch signature check, schema validation, head sampling |
| 2. Process | `core` pipeline | Decode, normalise timestamps to UTC, enrich with sensor metadata, route by channel |
| 3. Store | `segments` writer | Append-only segment files, daily indexes, compaction and retention |
| 4. Query | `query` HTTP API | Read-only scans over segments, pagination, signed health summaries |

Data flow:

```
sensor --> TLS 8443 --> edge --> bounded queue --> core --> segments --> query API
                                                         \--> plugin host (sidecar processes)
```

Design constraints that shaped the split:

- **The ingest path must never block on disk.** A slow or full store pushes
  back through a bounded queue and the edge sheds load; it does not buffer
  without limit.
- **Raw events are immutable.** Corrections are appended as new records with a
  `supersedes` field, never written in place.
- **Plugins are untrusted.** Team-supplied parsers run outside the core process
  with an explicit capability manifest and a memory limit.
- **The query path is read-only.** It cannot mutate segments, trigger
  compaction, or reach the network.

The full component description, threading model, queue semantics and failure
domains are in [docs/architecture.md](docs/architecture.md).

## 2. Key design decisions

Each decision is recorded as a short ADR. Read them in order; later decisions
assume the earlier ones.

1. [ADR 0001 — Append-only segmented event log](docs/decisions/0001-append-only-event-log.md).
   Raw events are written once to immutable segments with a per-day index;
   replay and audit read the same bytes the ingest path wrote. Compaction is a
   background rewrite into new segments, never an in-place update.
2. [ADR 0002 — Out-of-process plugins](docs/decisions/0002-out-of-process-plugins.md).
   Parsers contributed by other teams are separate supervised processes that
   talk to the core over a length-prefixed Unix-socket protocol and receive
   only the fields named in their capability manifest.
3. [ADR 0003 — Head sampling and tiered retention](docs/decisions/0003-sampling-and-retention.md).
   The high-volume `debug` channel is sampled at the edge with a deterministic
   hash of sensor id and UTC day; `error` and `audit` channels are never
   sampled. Retention is 14 days for raw segments, with daily compaction into
   one segment per channel per day.

## 3. Configuration

Fernlight deliberately has a small configuration surface: twelve environment
variables, all parsed once at start-up, with no hot reload. Unknown variables
are rejected so that a typo cannot silently disable a safeguard.

A documented sample lives in [config/.env.example](config/.env.example). It
contains example and placeholder values only — it is not a live configuration
and none of its values are usable credentials. The variables fall into four
groups:

- **Listeners** — `FERNLIGHT_INGEST_BIND`, `FERNLIGHT_QUERY_BIND`,
  `FERNLIGHT_INGEST_TLS_CERT`, `FERNLIGHT_INGEST_TLS_KEY`.
- **Storage** — `FERNLIGHT_STORE_ROOT`, `FERNLIGHT_SEGMENT_MAX_MB`,
  `FERNLIGHT_RETENTION_DAYS`.
- **Pipeline** — `FERNLIGHT_SAMPLE_RATE_DEBUG`, `FERNLIGHT_PLUGIN_DIR`,
  `FERNLIGHT_PLUGIN_MEMORY_MB`.
- **Access** — `FERNLIGHT_QUERY_TOKEN`, `FERNLIGHT_SIGNING_KEY`.

Deployment topology, ports, health endpoints, the restart runbook and the
capacity table are in [docs/operations.md](docs/operations.md).

## 4. Further reading

- [docs/architecture.md](docs/architecture.md) — components, queues, failure domains.
- [docs/decisions/](docs/decisions/) — the three ADRs summarised above.
- [docs/operations.md](docs/operations.md) — deployment and operations.
- [config/.env.example](config/.env.example) — sample configuration surface.
