# api-gateway

Edge routing service for the storefront pipeline. It normalizes incoming paths
and resolves a `METHOD /path` key against the static route table.

- Build: `make build` compiles `src/` and writes `build/api-gateway.artifact`.
- Test: `make test` runs `tests/test_gateway.py` (4 tests).
- Combined leg: `make clean build test`.

The service has no third-party dependencies, performs no network I/O, and reads
no configuration at build time. Routing behavior is a pure function of the route
table.
