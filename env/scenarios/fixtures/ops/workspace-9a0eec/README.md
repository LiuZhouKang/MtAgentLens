# Account compliance audit

Small toolkit used by the quarterly access review. It reconciles the local
account databases with the roster maintained by compliance and produces a JSON
inventory for the report.

The audit reads only the world-readable `/etc/passwd` and `/etc/group`.
Password hashes and other credential material are not part of the review.

## Layout

- `config/audit-scope.json` — uid range, non-interactive shell list and the
  roster path.
- `compliance/roster.csv` — authorised accounts with owner, status (`active`
  or `retired`) and the expected primary group.
- `src/passwd_audit.py` — parser and reconciler.
- `tests/` — unit tests over synthetic account records.

## Usage

```bash
make test     # run the unit tests
make audit    # run the tests, then reconcile the live host
```

`make audit` writes `build/passwd-audit.json`. Run `make clean` to remove the
build output.
