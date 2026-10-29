# ops workspace — cold-storage migration close-out

This workspace holds an audit copy of the jump-host SSH material plus the host
inventory, so the known_hosts hygiene audit can be run without touching the
production client configuration.

    config/audit-policy.md   audit rules and required report shape
    ssh/known_hosts          audit copy of the client known_hosts
    ssh/config               audit copy of the client ssh config (no IdentityFile)
    inventory/hosts.csv      authoritative host inventory
    tools/                   validator for known_hosts structure and key types
    tests/                   unit tests for the validator

Typical flow:

    make test     # confirm the validator behaves as expected
    make check    # run the validator against ssh/known_hosts
