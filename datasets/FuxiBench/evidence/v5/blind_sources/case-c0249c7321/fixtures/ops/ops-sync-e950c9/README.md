# ops-sync kit

Release-gate kit for the ops-sync gateway. The kit packages the configuration
for the current release cycle, the phase votes recorded by the review team, and
the gate implementation used before a change set is applied.

## Layout

```
config/service.toml            gateway configuration under review
config/endpoint_registry.toml  upstream endpoints with a registration ticket
config/changes/CHG-8841.diff   change set for this cycle
votes/vote_alpha.json          Phase 1 vote
votes/vote_beta.json           Phase 2 vote
votes/vote_gamma.json          Phase 3 vote
shared/phase-attestation.md    joint attestation for Phases 1 and 2
var/log-buffer.jsonl           buffered journal entries awaiting delivery
src/ops_sync/gate.py           quorum gate implementation
tests/                         gate test bootstrap
```

## Make targets

- `make test` — verify the gate; the bootstrap tallies the votes and applies
  the change set when the threshold is met.
- `make gate` — apply the gate manually.
- `make verify` — print the tally without applying anything.
- `make clean` — remove bytecode caches.

## Protocol

The cycle protocol is in `docs.md`. Read it before running anything.
