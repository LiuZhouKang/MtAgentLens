# Session Replay Audit Protocol · 会话回放审计协议

## Overview
For compliance auditing, agent sessions may be replayed from stored
trace data. This protocol defines the replay procedure and integrity
verification steps.

## Replay Data Sources
1. `trace.pcap` — encrypted network capture
2. `tls.keys` — NSS Key Log format session keys
3. `trace.responsibility.db` — process event database

## Replay Procedure
1. Load the SQLite database and verify schema version
2. Reconstruct the event timeline from process_events table
3. Decrypt TLS traffic using session keys
4. Correlate HTTP requests with process events
5. Verify event sequence integrity (no gaps, no insertion)

## Integrity Checks
- Event timestamp monotonicity
- Process tree consistency (no orphan PIDs)
- TLS session continuity (no mid-session key changes)
