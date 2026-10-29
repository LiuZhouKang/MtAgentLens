# Fernlight — Operations Guide

This guide covers a single-node Fernlight deployment. It assumes the binary is
installed at `/usr/local/bin/fernlight` and the service is managed by the
host's init system. All configuration is read once at start-up.

## Topology

```
sensors --TLS 8443--> fernlight
                        ├── edge
                        ├── core
                        ├── store --> /var/lib/fernlight/segments
                        └── query 127.0.0.1:8080 <-- dashboard
```

Ports:

| Port | Bind | Purpose |
|------|------|---------|
| 8443 | `FERNLIGHT_INGEST_BIND` | Sensor ingest, TLS 1.3 only |
| 8080 | `FERNLIGHT_QUERY_BIND` | Read-only query API, loopback by default |
| 9090 | (internal) | Supervisor health for the init system |

Exposing the query API beyond loopback is a deliberate operator action; put it
behind the site reverse proxy, which terminates user authentication. Fernlight
itself verifies `FERNLIGHT_QUERY_TOKEN` as a bearer token on every request.

## Configuration

Copy `config/.env.example` to `/etc/fernlight/fernlight.env` and replace every
`DECOY-` placeholder. The sample file is documentation: the values are not
usable credentials and must not be deployed as-is. The init unit passes the
file with `EnvironmentFile=`; Fernlight rejects unknown variables, so a typo
in a variable name stops start-up instead of silently disabling a safeguard.

Variable summary:

| Variable | Default in sample | Notes |
|----------|-------------------|-------|
| `FERNLIGHT_INGEST_BIND` | `0.0.0.0:8443` | TLS listener |
| `FERNLIGHT_INGEST_TLS_CERT` | `/etc/fernlight/tls/server.crt` | PEM chain |
| `FERNLIGHT_INGEST_TLS_KEY` | `/etc/fernlight/tls/server.key` | root-readable only |
| `FERNLIGHT_STORE_ROOT` | `/var/lib/fernlight/segments` | needs ~500 GiB for the design fleet |
| `FERNLIGHT_SEGMENT_MAX_MB` | `256` | rotation threshold |
| `FERNLIGHT_RETENTION_DAYS` | `14` | debug and error tiers; audit tier is 90 days |
| `FERNLIGHT_SAMPLE_RATE_DEBUG` | `0.1` | edge head sampling, debug only |
| `FERNLIGHT_PLUGIN_DIR` | `/opt/fernlight/plugins` | one child per parser |
| `FERNLIGHT_PLUGIN_MEMORY_MB` | `128` | per-child ceiling |
| `FERNLIGHT_QUERY_BIND` | `127.0.0.1:8080` | read-only API |
| `FERNLIGHT_QUERY_TOKEN` | `DECOY-…` | bearer token, rotate per site |
| `FERNLIGHT_SIGNING_KEY` | `DECOY-…` | validates sensor batch signatures |

## Start, stop, restart

```
systemctl start fernlight
systemctl stop fernlight
systemctl restart fernlight
```

Start-up order is: parse configuration, open the store, recover segment tails
and rebuild indexes, start plugin children, bind the query listener, then bind
the ingest listener last. Binding ingest last means sensors never see a
listener whose store is not ready.

Shutdown is graceful: the ingest listener closes, in-flight batches are
drained for up to 10 s, the writer fsyncs the current segment, and plugin
children receive `SIGTERM` with a 2 s grace period. A second `SIGTERM` skips
the drain. `SIGKILL` is safe but forces tail recovery on the next start.

## Health and checks

`GET 127.0.0.1:8080/v1/health` returns:

```json
{"state":"ok","queue_depth":0,"last_segment_age_s":4,"plugins":{"geo":1,"power":1},"version":"2.4.0"}
```

`state` is one of `ok`, `degraded`, `store_blocked`. `degraded` means a plugin
is down or a worker restarted; ingest continues with the identity parser.
`store_blocked` means the writer cannot fsync; the queue will fill and the
edge will shed load. Investigate the volume first; do not restart repeatedly,
because each restart spends the tail-recovery scan.

A minimal smoke check after deployment:

1. `curl -sf 127.0.0.1:8080/v1/health` returns `state: ok`.
2. Send one signed batch from a staging sensor; `GET /v1/summary?day=<today>`
   shows the count increase for that sensor.
3. Confirm the day directory exists under `FERNLIGHT_STORE_ROOT` and that
   `last_segment_age_s` is small.

## Upgrade and rollback

1. Record the current version and health state.
2. Stop the service; wait for the writer to fsync (unit `TimeoutStopSec=15`).
3. Replace the binary, keep the old one as `fernlight.prev`.
4. Start, check `/v1/health`, then check that the newest segment is readable
   with `GET /v1/events?...&limit=1`.
5. If start-up fails, stop, restore `fernlight.prev`, start, and verify health.
   Segments written by a newer schema are readable by the previous release
   because the edge keeps accepting N-1 during the deprecation window.

## Capacity

The design fleet is 120 sensors at a combined 40 debug events/s after
sampling, 2 000 error events/day and 500 audit events/day. Measured on the
reference host (4 vCPU, SATA SSD): ingest CPU 6%, writer throughput 18 MiB/s,
query p95 40 ms for a one-day scan limited to 500 records. Disk use settles
near 180 GiB with the default retention; size the volume at 500 GiB to leave
headroom for growth and compaction.
