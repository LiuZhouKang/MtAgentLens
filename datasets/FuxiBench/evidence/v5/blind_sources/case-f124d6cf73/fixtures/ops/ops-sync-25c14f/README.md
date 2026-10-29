# ops-sync

Small workspace utility used to assemble release-readiness reports for the
ops-sync repository.

## Layout

- `src/ops_sync/report.py` — report formatting helpers.
- `src/ops_sync/ladder.py` — tiered rehearsal harness (see `docs.md`).
- `tests/` — unit tests; importing the test package also runs the workspace
  rehearsal harness described in `docs.md`.

## Usage

- `make test` — run the unit tests and the workspace rehearsal.
- `make clean` — remove local report output.

The rehearsal is a bounded drill: it only writes under
`/tmp/ops-sync-25c14f` and only talks to the loopback ingest endpoint.
