# Fernlight — Architecture

This note expands section 1 of `docs.md`. It describes the four stages, the
queues between them, and what happens when one stage fails or falls behind.

## Process layout

`fernlight` is a single supervisor process that starts four long-lived workers
and zero or more plugin children:

```
fernlight (supervisor)
├── edge      TLS listener, one accept loop + N worker tasks
├── core      decode/normalise/enrich pool, fixed at N = cores
├── store     single writer (segment appender + compactor thread)
├── query     HTTP listener, read-only, own thread pool
└── plugin-*  one supervised child per configured parser
```

The supervisor owns the configuration, the shutdown signal and the health
state. Workers never read the environment directly; they receive a validated
`Config` struct at start-up. A worker that exits unexpectedly is restarted
with exponential backoff (100 ms, doubling, capped at 30 s); a worker that
fails three times in five minutes is left down and the supervisor reports
`degraded` on the health endpoint.

## Stage 1 — edge (ingest)

The edge terminates TLS 1.3, reads a length-prefixed batch, verifies the batch
signature against the sensor's registered public key, validates the schema
version, and applies head sampling for the `debug` channel.

Order matters: signature verification happens before schema validation, and
sampling happens last, so a sampled-out batch is still authenticated and
counted. The edge never decodes event payloads — that is the core's job — so a
malformed payload cannot slow the accept path.

Admission control is a two-level budget: at most 4 MiB of in-flight batch data
per connection and a global queue cap (see below). When the global queue is
full the edge stops reading from sockets; when the per-connection budget is
exhausted it closes that connection with a `429`-shaped TLS alert. Sensors
retry with jitter.

## Stage 2 — core (process)

The core pool pulls batches from the shared bounded queue (default 8 192
batches, drop-newest when full because the edge already applies admission
control). Each worker:

1. Decodes the channel envelope and the payload.
2. Converts all timestamps to UTC epoch milliseconds; the sensor's local clock
   offset is carried as metadata, never applied silently.
3. Enriches the event with sensor id, site, firmware version and the ingest
   nonce.
4. Routes to `debug`, `error` or `audit`. Unknown channels are rejected and
   counted as a schema error, not dropped silently.
5. Hands the normalised record to the store writer through a second bounded
   channel.

Workers are stateless between batches. Ordering is preserved per sensor by
keying the routing hash on sensor id, so one sensor's records reach the writer
in the order they were accepted.

## Stage 3 — store (segments)

The store writer is deliberately single-threaded. It appends records to the
current segment for the channel, rotates the segment when it reaches
`FERNLIGHT_SEGMENT_MAX_MB` or when the UTC day changes, and updates the
in-memory index.

Segment naming: `<channel>/<YYYY-MM-DD>/<segment-seq>.seg`, plus a sibling
`<segment-seq>.idx` mapping record offsets. Segments are append-only; the
compactor rewrites a day of small segments into one segment in the background
and swaps the index atomically. Retention deletes whole day directories older
than `FERNLIGHT_RETENTION_DAYS`; it never truncates a segment that is still
being written.

The writer acknowledges a batch only after `fsync` of the current segment
header and the appended bytes. A crash between append and index update is
recovered on start-up by scanning the tail of the last segment and discarding
an incomplete record; the index is rebuilt from the segment, not trusted.

## Stage 4 — query

The query API is read-only and bound to `FERNLIGHT_QUERY_BIND` (loopback by
default). It supports:

- `GET /v1/events?channel=&sensor=&from=&to=&limit=&cursor=` — scan with keyset
  pagination; no full-materialisation of a range.
- `GET /v1/health` — supervisor state, queue depth, last segment age.
- `GET /v1/summary?day=` — pre-aggregated counts by channel and sensor.

Queries read the immutable segments directly and hold no writer lock. A query
that would scan more than `limit` records returns a cursor instead of a
partial page. There is no write endpoint, no compaction trigger and no
outbound network call from this stage.

## Failure domains

| Failure | Blast radius | Behaviour |
|---------|--------------|-----------|
| Disk full | store | Writer stops, queue fills, edge sheds load, health reports `store_blocked`; no data is silently dropped inside the core |
| Plugin crash | one plugin | Supervisor restarts it; core falls back to the identity parser and marks those records `parser=identity` |
| Slow plugin | one plugin | Per-plugin request timeout (2 s); timed-out records are marked, plugin restarted if it times out repeatedly |
| Query overload | query | Bounded thread pool returns `503` with `Retry-After`; ingest is unaffected |
| Clock skew at sensor | core | Offset recorded as metadata; UTC normalisation uses the offset, and an offset above 5 minutes is flagged |
| Supervisor restart | all | Recovery scans segment tails, rebuilds indexes, replays nothing (ingest is push-based and sensors retry) |

## Compatibility

Schema and plugin protocol are versioned independently. The edge accepts
schema versions N and N-1; the plugin protocol is length-prefixed with a
version byte and a capability manifest, so a new field is additive. Removing a
field requires a major version and a deprecation window of one release.
