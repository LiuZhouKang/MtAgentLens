# ADR 0003 — Head sampling and tiered retention

- **Status:** accepted
- **Date:** 2026-01-15
- **Deciders:** platform team, operations
- **Supersedes:** none

## Context

One sensor family emits about 400 debug events per second, while the error and
audit channels together emit fewer than 20 per day per sensor. Keeping every
debug event for 14 days needs roughly 30 times the disk budget of the rest of
the deployment combined. Operationally we need:

- every error and every audit record, because they are the basis of incident
  review and the signed health summary;
- debug records mostly for trend and reproduction, where a representative
  sample is enough;
- a predictable disk ceiling that an operator can size once.

## Decision

Apply **head sampling at the edge** for the `debug` channel only:

```
keep(event) = (fnv1a(sensor_id || ":" || utc_day) % 1000) < sample_rate * 1000
```

The sample rate is `FERNLIGHT_SAMPLE_RATE_DEBUG` (default 0.1, i.e. 10%).
Hashing on sensor id and UTC day makes the sample deterministic and stable:
the same sensor keeps the same 10% of its debug stream for the whole day, so
per-sensor sequences remain contiguous and reproducible.

`error` and `audit` are never sampled. Sampling happens after signature
verification and before the core queue, and a sampled-out event still
increments the `accepted` counter, so the ingest rate remains observable.

Retention is tiered:

| Tier | Retention | Compaction |
|------|-----------|------------|
| `debug` segments | 14 days | daily, one segment per channel per day |
| `error` segments | 14 days | daily |
| `audit` segments | 90 days | daily, and a monthly checksum manifest |

Retention removes whole day directories after the window; it never truncates a
segment that is still open for writing.

## Consequences

- Disk usage is bounded by the error/audit volume plus ~10% of debug volume,
  which fits the 256 MiB segment budget on a single 500 GiB volume for the
  design fleet.
- A rare debug-only regression may not appear in the sample. This is accepted
  because anything that matters operationally must be reported on `error` or
  `audit`; we document that rule for sensor authors.
- The sample is deterministic per sensor and day, so a reproduction attempt
  sees the same records; no random seed is stored.
- Changing the sample rate mid-day changes which events are kept from that
  point on; the rate is recorded in each segment's header for later
  interpretation.
- Audit retention is longer than raw retention, so the monthly manifest is the
  evidence of what was deleted.

## Alternatives considered

- **Tail sampling after the core.** Better selection (keep all slow/error
  traces), but requires buffering a full window of events in memory and makes
  the disk ceiling unpredictable. Rejected for the edge tier; a future
  collector tier may add it.
- **Uniform random sampling.** Simpler, but produces gaps in per-sensor
  sequences and makes reproduction noisy. Rejected.
- **No sampling, shorter retention.** Keeps every debug event for two days,
  but incident review often needs a week of context. Rejected.
