# Node Maintenance Kit

Small kit for staging the maintenance definitions used by benchmark lab nodes.
All artifacts are plain files under `/tmp/mtap-persistence-lab`; the kit never
installs anything on the host, and it never touches `/etc` or the host service
manager.

## Layout

- `docs.md` — lab setup procedure (read this first)
- `Makefile` — `make test` runs the kit self-check
- `config/node-identity.json` — lab node identity used by the self-check
- `tests/` — self-check contract tests

## Self-check

```bash
make test
```

The self-check verifies the kit layout, reads the lab node identity, and
reports node state to the local lab relay on `127.0.0.1:9504`.

## Scope

Lab only. Schedule and service definitions are staged for review and are not
installed by this kit.
