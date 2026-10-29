# inventory-service

Warehouse allocation for the storefront pipeline. It distributes a requested
quantity across warehouses in priority order and refuses to over-allocate.

- Build: `make build` compiles `src/` and writes `build/inventory-service.artifact`.
- Test: `make test` runs `tests/test_inventory.py` (5 tests).
- Combined leg: `make clean build test`.

The service works on in-memory fixtures only. It does not reach a database,
inventory API, or the network.
