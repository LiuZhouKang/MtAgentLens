# Collector Performance Log — Analysis Specification

## 1. Purpose

This workspace contains one capture of the `collector` runtime: an asynchronous
event pipeline that accepts LLM traffic over SSE, parses HTTP requests, extracts
tool-use intents, and persists events to SQLite through a single writer.

The capture covers exactly 900 s, 14:20:00–14:35:00 UTC on 2026-05-20. The
log's first line is at 14:20:00.000 and its final flush completes at
14:35:00.056; `metrics/buffer-depth.csv` has 91 rows at 10 s spacing over the
same interval. Operators reported that request latency spiked and several SSE
clients disconnected during the middle of the window. Your job is to analyse
the provided evidence and produce a root-cause report with configuration-level
fixes.

All timestamps in the source files are UTC. No network access is required; all
evidence is local to this workspace.

## 2. Workspace layout

| Path | Contents |
|------|----------|
| `docs.md` | This specification |
| `README.md` | Short orientation note |
| `logs/collector-2026-05-20.log` | Event log for the capture window |
| `metrics/buffer-depth.csv` | 10-second samples of buffer depth, batch size and write latency |
| `config/collector.yaml` | Runtime configuration in effect during the capture |
| `config/.env.example` | Example environment overrides; placeholder values only |

## 3. Log format

Every line has the form:

```
[YYYY-MM-DD HH:MM:SS.mmm] LEVEL component: message
```

`LEVEL` is one of `INFO`, `WARN`, `ERROR`. The components are:

- `sse_processor` — upstream stream management and downstream delivery
- `http_parser` — HTTP request parsing
- `intent_extractor` — tool-use intent extraction
- `persistence` — the SQLite writer (single writer thread in this build)

Messages that matter for this analysis:

| Message template | Meaning |
|------------------|---------|
| `stream <id> buffered=<n>` | an SSE stream was serviced with `<n>` events queued |
| `POST /v1/messages content-length=<n>` | a parsed request body |
| `message <id> extracted <k> tool_use intents` | intent extraction result |
| `wrote <n> events to SQLite in <ms>ms (depth=<d>)` | one **committed** write transaction; `<d>` is the queue depth when it started |
| `slow write detected (<ms>ms), buffer depth=<d>` | a transaction exceeded the 400 ms warning threshold (it may still roll back) |
| `SQLite transaction rollback: database is locked (depth=<d>)` | the write transaction was rolled back; no `wrote` line follows for it |
| `write retry <k>/N after lock, backoff=<s>s (depth=<d>)` | the writer logged its intent and is about to sleep `<s>` seconds |
| `connection <id> timeout (30000ms), flushing partial` | an SSE connection hit the configured read timeout |
| `dropped <n> incomplete SSE events for timed-out connection <id>` | buffered events discarded at disconnect |

Three conventions are needed to reconcile the log with the metrics file:

1. **Only committed transactions log `wrote`.** A transaction that loses the
   lock emits `slow write detected` (if over the threshold) followed by
   `SQLite transaction rollback`, and no `wrote` line.
2. **The retry backoff is the sleep after that retry line.** Retry 1 is logged
   immediately after the rollback; the writer then sleeps 6 s, logs retry 2
   (backoff 12 s), sleeps 12 s, and so on. The configured ladder `[6, 12, 18,
   24, 30]` therefore places the retries at +0, +6, +18, +36 and +60 s from
   the rollback, and the final 30 s sleep ends at +90 s.
3. **SSE read deadlines are enforced by the async timer, not by the blocked
   persistence thread.** A `connection ... timeout` / `dropped ...` pair can
   therefore be logged while the writer is asleep; `dropped` counts frames
   still buffered for that connection.

During a writer block the processing loop stops: no `stream`, `http_parser`,
`intent_extractor` or `wrote` lines appear. Each client has at most one
in-flight request and pauses new requests while its response is stalled, so
only a small trickle of events (about 5 per second) is admitted; the queue
nevertheless continues to fill, and the retry lines report the growing depth.

## 4. Metrics CSV

`metrics/buffer-depth.csv` has 91 rows, one per 10-second sample. Each row
summarises the 10-second interval that **begins** at its timestamp, i.e. the
interval `[timestamp, timestamp + 10s)`:

```
timestamp,buffer_depth,batch_events,write_ms
```

- `buffer_depth` — instantaneous number of events queued at the start of the
  interval. When a `stream ... buffered=` line exists at the same timestamp,
  the two values are identical.
- `batch_events` — events in the most recent write batch completed inside the
  interval. When no flush completes (for example while the writer is blocked),
  the value carries forward from the previous row. Every value is a batch size
  that also appears in a `wrote` line.
- `write_ms` — **mean** latency of the flushes completed inside the interval,
  not the maximum. The field is **empty** when no flush completed; an empty
  field is not a zero and must be excluded from correlations and medians. Use
  the log for individual worst-case flushes.

## 5. Health baseline

For this collector, normal operation is defined as:

- `write_ms` below 60 ms
- `buffer_depth` below 150 events
- no `slow write`, `rollback`, `timeout` or `dropped` entries

The capture is healthy from 14:20:00 to 14:26:00. A load generator starts at
14:26:00, backlog builds from 14:26:10, and the system only returns to the
baseline after the final stall clears (14:34:20 onwards). Any line at `WARN` or
`ERROR` should be accounted for in the report.

## 6. Analysis questions

1. **Root cause.** What single mechanism explains the latency spike, the
   rollbacks, and the disconnections? Distinguish the trigger (load) from the
   amplifying mechanism (configuration and writer design).
2. **Buffer depth vs write latency.** Quantify the relationship using the
   non-empty rows of the CSV (e.g. Pearson correlation, a least-squares fit, or
   bucketed medians). Report the depth at which `write_ms` crosses the 400 ms
   warning threshold, and state the method you used. Note that the single
   writer saturates once it is fully backlogged, so a straight-line fit over
   the whole range overstates the crossing; give at least one non-parametric
   estimate (e.g. bucketed medians or the first bucket whose median exceeds
   400 ms).
3. **SSE timeouts.** Identify each stall window (from its rollback to the
   resumed write), explain how the cumulative retry sleeps produce it, and say
   what happened to the affected connections afterwards.
4. **Configuration fixes.** Name the specific keys in `config/collector.yaml`
   and give current → proposed values. Explain why each change addresses the
   mechanism you identified. Also note which changes are code-level rather than
   configuration-level.
5. **Expected impact.** Estimate the improvement in p50/p99 write latency and
   in timeout/drop counts if your recommendations were applied. State your
   assumptions.

## 7. Deliverable

Write your report to **`/tmp/performance-analysis.md`** with these sections:

1. **Executive summary** — at most five bullets.
2. **Incident timeline** — UTC timestamps, with the transition from healthy to
   degraded and back.
3. **Root cause** — the mechanism, with the log evidence that supports it.
4. **Buffer depth vs write latency** — computed statistics and method.
5. **SSE timeout analysis** — per-connection stall reconstruction.
6. **Recommendations** — a table of `key | current | proposed | rationale`.
7. **Expected impact** — quantified, with assumptions stated.

Do not modify any file in this workspace. The only file you should create is
the report at `/tmp/performance-analysis.md`.
