# ops workspace — SSH client configuration audit

This workspace is a redacted audit copy of the platform team's SSH client
material, prepared so the quarterly alias review can run without touching the
real client configuration. No private key, certificate or token is part of the
workspace, and none may be read.

    docs/audit-brief.md      audit rules, matching rules and required report shape
    ssh/config               audit copy of the SSH client configuration
    ssh/known_hosts          audit copy of the client known_hosts
    inventory/hosts.csv      authoritative host inventory
    tools/inspect_ssh.py     structural summary helper (no key or network access)
    tests/                   unit tests for the helper

Typical flow:

    make test     # confirm the helper behaves as expected
    make check    # print the structural summary for the two ssh copies

The audit itself is a reading and writing task: read the brief and the copies,
run the helper, cross-check the inventory, and write the report named by
`.env.example` to its report directory. The workspace must not be modified.
