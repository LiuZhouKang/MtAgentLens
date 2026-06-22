# MtAgentPrism REST API Reference

## GET /api/events

Returns paginated agent events with optional filtering.

Query Parameters:
- session_id (string, required): Session identifier
- from (int64, optional): Start timestamp in nanoseconds
- to (int64, optional): End timestamp in nanoseconds
- event_type (string, optional): Filter by event type (EXEC, EXIT, TLS, FILE)
- limit (int, optional, default=100): Max events per page
- offset (int, optional, default=0): Pagination offset

Response 200:
```json
{
  "events": [
    {
      "id": "uuid",
      "timestamp_ns": 1234567890,
      "source": "ssl",
      "event_type": "TLS",
      "data": { "connection_id": "...", "size": 1024 }
    }
  ],
  "total": 5000,
  "has_more": true
}
```

## GET /api/graph

Returns the responsibility graph for a session.

Query Parameters:
- session_id (string, required)
- depth (int, optional, default=3): Max graph traversal depth

## GET /api/alerts

Returns detected anomalies and alerts.

Query Parameters:
- session_id (string, required)
- severity (string, optional): Filter by severity (Info, Warning, High, Critical)
