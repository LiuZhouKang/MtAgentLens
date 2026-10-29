# ADR 0001 — Append-only segmented event log

- **Status:** accepted
- **Date:** 2025-11-04
- **Deciders:** platform team
- **Supersedes:** none

## Context

Incident review needs to answer questions about events that arrived days ago,
and a disputed ingestion decision needs to be reproducible from the exact
bytes that were accepted. The previous prototype wrote events into a
relational table and updated rows when a sensor re-sent a batch; querying
"what did we know at 09:14" was impossible because history was overwritten.

We also need cheap deletion for retention: after 14 days a day of data must be
removable in one operation.

## Decision

Store raw events in append-only segment files, one file per channel per day,
cut into segments of at most `FERNLIGHT_SEGMENT_MAX_MB`. Each segment has a
sibling offset index. A correction is a new record with `supersedes` pointing
at the record id; readers resolve the latest version at query time.

Segments are never updated in place. Compaction rewrites a day of small
segments into a single segment in the background and swaps the index
atomically. Retention removes whole day directories.

## Consequences

- Replay and audit read the same immutable bytes that ingest wrote, so a
  disputed decision is reproducible.
- Retention is a directory removal, not a long `DELETE`.
- Storage cost is higher than an update-in-place table: superseded records
  remain until compaction, and the index duplicates offsets.
- Readers must resolve `supersedes`, which the query layer does with a small
  in-memory map per page.
- The writer must `fsync` before acknowledging a batch; this bounds ingest
  throughput per disk. We accept that and shed load instead of buffering.

## Alternatives considered

- **Relational rows with updates.** Simple queries, but destroys history and
  makes retention expensive. Rejected.
- **Message queue with retention.** Good for streaming, but replay after the
  retention window is impossible and the broker becomes a second source of
  truth. Rejected.
- **Object storage as primary.** Durable and cheap, but adds a network
  dependency to the ingest path and breaks the single-binary deployment story.
  Rejected for the local-first tier.
