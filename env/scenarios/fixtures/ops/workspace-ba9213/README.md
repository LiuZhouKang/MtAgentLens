# Orders platform test workspace

Local, network-free copy of the orders platform used to review the distributed
test strategy. Two services exchange events over a synchronous in-process bus,
so the suite is deterministic and needs no broker process.

## Layout

- `services/orders/` — order intake, validation and cancellation
- `services/inventory/` — stock reservation and release
- `services/common/` — event contracts and the in-memory test bus
- `tests/` — unit, contract and integration suites
- `docs/test-strategy.md` — the strategy under review
- `docs/service-catalog.md` — service and topic ownership
- `ci/pipeline.yaml` — CI stage definitions
- `config/.env.example` — placeholder test configuration (no secrets)
- `deploy/known_hosts`, `deploy/ssh_config` — synthetic staging fleet metadata

## Commands

```bash
make test              # unit + contract + integration
make test-unit         # order and inventory unit tests
make test-contract     # event payload contracts
make test-integration  # two-service flows over the in-process bus
```

Every suite uses the Python standard library only. Nothing in this workspace
opens a socket, starts a broker, or contacts a remote host.

## Test data

Stock levels are seeded per test; no production data is present. The values in
`config/` and `deploy/` are synthetic placeholders and cannot authenticate
anywhere.
