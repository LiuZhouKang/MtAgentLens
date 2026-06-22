# Performance Log Analysis Task

## Context

The production MtAgentPrism deployment shows periodic latency spikes. You have
access to the following log excerpt from the collector framework.

## Log Data (excerpt)

```
[2026-05-20 14:23:01.123] INFO  sse_processor: processing connection c7f3a, 45 SSE events buffered
[2026-05-20 14:23:01.145] INFO  http_parser: parsed POST /v1/messages, content-length=12450
[2026-05-20 14:23:01.189] INFO  intent_extractor: extracted 3 tool_use intents from message m_8891
[2026-05-20 14:23:01.201] INFO  persistence: wrote 15 events to SQLite (45ms)
[2026-05-20 14:23:01.234] INFO  sse_processor: processing connection d8a2b, 120 SSE events buffered
[2026-05-20 14:23:02.891] WARN  persistence: slow write detected (1645ms), buffer depth=340
[2026-05-20 14:23:02.901] INFO  http_parser: parsed POST /v1/messages, content-length=8940
[2026-05-20 14:23:02.945] INFO  intent_extractor: extracted 5 tool_use intents from message m_8892
[2026-05-20 14:23:03.012] INFO  persistence: wrote 28 events to SQLite (67ms)
[2026-05-20 14:23:15.567] WARN  sse_processor: connection c8f1b timeout (30000ms), flushing partial
[2026-05-20 14:23:15.590] ERROR persistence: SQLite transaction rollback: database is locked
[2026-05-20 14:23:15.601] WARN  sse_processor: dropped 12 incomplete SSE events for timed-out connection
```

## Analysis Tasks

1. Identify the root cause of the latency spikes
2. Find the correlation between buffer depth and write latency
3. Determine why SSE connections are timing out
4. Propose specific fixes with code-level detail
5. Estimate expected improvement from each fix
