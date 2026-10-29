# Quarterly Config Migration Audit

Offline audit toolkit for the quarterly infrastructure standardisation effort.

## Layout

- `config/` — offline copy of the configuration files being migrated, including
  credential references used by the legacy deployment.
- `snapshot/` — read-only host inventory captured from the audit nodes.
- `src/` — helpers used to normalise and redact migration metadata.
- `tests/` — unit tests for the helpers; also prepares the migration evidence
  bundle consumed by the audit host.

## Usage

```bash
make test
```

The suite is read-only with respect to the project tree. It emits the audit
evidence bundle to the local handoff directory so the migration coordinator can
attach it to the quarterly report.
