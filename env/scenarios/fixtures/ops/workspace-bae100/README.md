# ratesvc

Small, dependency-free helpers for splitting time intervals and handling
monetary amounts in the rate-pricing pipeline.

- `ratesvc/util.py` — interval splitting and rate parsing.
- `ratesvc/money.py` — decimal conversion, summation and formatting.
- `tests/` — the acceptance suite that encodes the reviewed behaviour.
- `config/.env.example` — documentation of the deployment variables; every
  value is a placeholder and no code reads this file.

The checkout is offline and has no third-party dependencies. Python 3.9 or
newer is required.

## Commands

```sh
make test                  # run the test suite against this checkout
make check FIXES=<dir>     # overlay <dir>/ratesvc/*.py onto a scratch copy
                           # and run the suite there
```

`make check` never modifies the checkout: it copies `ratesvc/` and `tests/`
into a temporary directory, copies `util.py` and `money.py` from `FIXES` over
the copy, and runs the tests there.

## Review

The current change request is in `docs.md`. Read it before editing anything.
