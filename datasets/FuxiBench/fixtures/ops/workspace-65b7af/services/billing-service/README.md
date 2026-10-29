# billing-service

Invoice arithmetic for the storefront pipeline. Amounts are handled as
`Decimal` values and rounded half-up at the cent boundary.

- Build: `make build` compiles `src/` and writes `build/billing-service.artifact`.
- Test: `make test` runs `tests/test_billing.py` (5 tests).
- Combined leg: `make clean build test`.

All fixtures are synthetic amounts. The service does no I/O, uses no payment
provider, and makes no network calls.
