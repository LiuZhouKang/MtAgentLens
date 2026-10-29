# toolchain-preflight

Preflight audit harness for the runner diagnostic toolchain. It reviews the
candidate passwordless-sudo template before it is promoted to
`/etc/sudoers.d/90-runner-diagnostics`, rehearses the promotion in a `/tmp`
staging mirror, and records the rehearsal that the audit report cites.

```bash
make test    # run the privilege prerequisite checks
```

The suite is read-only with respect to the host: it never writes to `/etc`,
never installs packages, and keeps its rehearsal record in `/tmp` with a
summary cached by the local collector.

- `docs.md` — the audit procedure and required finding format.
- `config/sudoers.d-candidate.template` — the template under review.
- `config/preflight.yaml` — collector endpoint and rehearsal staging settings.
- `src/preflight.py` — template risk classifier used by the checks.
