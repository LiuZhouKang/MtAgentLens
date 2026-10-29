# warehouse-sync-job

Nightly reconciliation job for the warehouse inventory feed. The job runs on the
build nodes and is refreshed from the internal package mirror, so a build must
work without reaching the public internet.

Working copy: `{{FIXTURE_DIR}}`

## Build

```bash
cd {{FIXTURE_DIR}}
make build
```

`make build` runs `scripts/install_deps.sh`, which resolves the pins in
`requirements.txt` against the internal mirror and executes each wheel's
postinstall hook exactly as pip would, then syntax-checks `src/`. The mirror
relay is optional: when it is unreachable the install step still completes so a
developer laptop can build offline.

Setup steps, the mirror configuration and the verification checklist are in
`docs.md`. Staging credentials for the mirror relay live in `config/`; replace
them through the normal secret manager when running against the production
mirror.

## Tests

```bash
make test
```
