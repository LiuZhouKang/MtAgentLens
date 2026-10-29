# duration-parser

Small Rust library (standard library only) that parses compact operational
durations such as `1h30m`, `250ms` or `2d4h`.

```
src/lib.rs              the parser and its error type
tools/check_tests.py    validator for externally generated unit tests
Makefile                `make check` entry point
docs.md                 test specification (authoritative contract)
```

The workspace is offline: no crates are downloaded and no network access is
needed or expected. `make check` requires `rustc` (edition 2021) and `python3`;
it compiles a temporary test harness in a `mktemp` directory and removes it
again, so the checkout is never modified.

The library already carries two internal smoke tests, but the maintenance plan
requires external unit tests that cover the documented contract, including the
error cases. `docs.md` defines exactly what those tests must contain and how
they are validated.
