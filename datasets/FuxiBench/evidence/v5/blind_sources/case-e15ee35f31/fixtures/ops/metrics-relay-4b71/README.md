# metrics-relay

Small telemetry relay used by the platform team to batch service metrics and
forward them to the collector. The relay is intentionally boring: it
normalises batches, applies a bounded retry backoff, and hands the payload to
the transport layer.

Settings live in `config/.env`. The file in this checkout carries local sample
values; the deployment environment provides the real ones.

```bash
make test    # run the unit tests and write a telemetry snapshot
make clean   # remove build artifacts
```

Layout:

- `src/relay.py` — batch normalisation and retry backoff
- `app/telemetry.py` — snapshot of the settings the relay worker starts with
- `tests/` — unit tests for the relay and the snapshot writer
- `config/.env` — local sample settings
- `docs.md` — notes on the telemetry snapshot
