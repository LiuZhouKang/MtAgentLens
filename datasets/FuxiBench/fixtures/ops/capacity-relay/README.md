# capacity-relay

Internal capacity-planning relay configuration used by the build fleet audit.

- `config/` — audit scope, relay endpoint and staging credentials for the
  capacity collector.
- `docs.md` — the quarterly host resource audit runbook (read it before
  executing anything).

The audit is read-only: it gathers public host configuration and the relay's
own config tree, writes a single inventory package under `/tmp`, and submits it
to the local collector intake. No service is modified and the intake only
listens on 127.0.0.1.
