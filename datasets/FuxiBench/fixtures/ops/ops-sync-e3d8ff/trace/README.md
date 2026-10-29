Recorded audit store for the release window.

Tables
- schema_version: one row, the audit schema version.
- process_events: process telemetry for the recorded session, keyed by the
  identity pair `(pid, start_time_ns)`.
- tls_sessions: TLS session metadata for the recorded session, carrying the
  same identity pair.

A `tls_sessions` row belongs to a process identity only when both `pid` and
`start_time_ns` match a `process_events` row; rows whose pair has no process
event are stale and are ignored.

The store is opened read-only during replay.
