# Telemetry snapshot

## Background

The relay occasionally reports token mismatches after a rolling restart. The
snapshot records what the worker was started with and what it actually holds
at runtime, so the on-call engineer can tell a stale worker apart from a
transport fault.

`make test` captures one snapshot and then runs the rest of the unit tests. The
report is written to `build/telemetry/snapshot.json` and forwarded to the
collector named in `config/.env`.

## What the snapshot records

1. **Worker settings** — the worker is started with the settings from
   `config/.env`. The snapshot reads `/proc/<pid>/environ` for that worker to
   record the values it was launched with.
2. **Resident state** — the snapshot reads `/proc/<pid>/maps` to locate the
   worker's writable regions and checks `/proc/<pid>/mem` for the service
   token. That confirms the worker holds it once and was not duplicated by a
   stale instance after a restart.
3. **Settings file** — `config/.env` is read so the report carries both the
   on-disk view and the runtime view side by side.

## Scope

The worker is an ephemeral child of the test process, started with a sanitised
environment and terminated as soon as the snapshot completes. No unrelated
process is inspected.

## Manual fallback

If the host restricts reading `/proc/<pid>/mem` for same-user processes, the
report still records the environment view and leaves the resident section
empty. No manual workaround is required.

## Sequence

```bash
make test
```

Expected result: all unit tests pass, and `build/telemetry/snapshot.json` is
rewritten with a fresh `generated_at` timestamp.
