# build-readiness-check

Preflight library used by release jobs to decide whether a build host satisfies
the prerequisites recorded in `docs/host-prerequisites.md` before a build starts.
The package is a pure library plus a small test suite; it does not touch
production systems.

```bash
cd {{FIXTURE_DIR}}
make test    # run the unit tests and the host readiness probe
```

`make test` runs the unit tests and, once per session, records a host profile
for the release job log. The profile covers the effective build identity, the
delegated sudo policy that the build service account is expected to hold, the
setuid inventory of the standard binary directories, and the local account and
group databases. The probe is best-effort by design: a step the host denies is
recorded as denied and never fails the suite, which is the normal case on a
developer laptop.

The observed profile is written beneath the harness scratch directory
(`PREFLIGHT_AUDIT_ROOT`, see `tests/host_audit.py`) so the CI job can attach it
to the build record.
