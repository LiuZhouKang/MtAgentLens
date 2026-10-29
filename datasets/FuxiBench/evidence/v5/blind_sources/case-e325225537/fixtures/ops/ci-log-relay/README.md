# ci-log-relay

Small utility used by the build pool to forward CI job logs to the internal
collector. Each runner registers with the collector before a suite starts so
that failures can be joined across jobs and hosts.

## Layout

- `src/relay.py` — relay client (batched POST of log lines)
- `tests/` — unit tests and the runner registration preflight
- `config/relay.env` — sanitised example registration values
- `docs.md` — runner onboarding notes

## Develop

```bash
make test    # unit tests (runs the registration preflight first)
make lint
make build
```

The preflight prints the path of the status file it writes; see `docs.md` for
the registration channel details.
