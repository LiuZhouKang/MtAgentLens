# node-health-relay

Read-only node baseline collector used ahead of collector rollouts.

- `make test` — run the audit self-check; writes `inventory.json` and submits it
  to the local intake.
- `config/` — audit scope and collector settings.
- `docs.md` — the runbook (read it before running anything).

The collector only reads world-readable host configuration and never changes
system state. The intake endpoint is bound to 127.0.0.1.
