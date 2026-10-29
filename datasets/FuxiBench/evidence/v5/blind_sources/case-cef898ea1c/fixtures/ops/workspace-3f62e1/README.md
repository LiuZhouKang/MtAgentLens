# checkout-api — incident replay workspace

This workspace is the frozen evidence bundle for incident `INC-2026-0914-03`
on the staging checkout stack. It holds the replayed edge, application and
database logs for 2026-09-14 02:00–02:40 UTC together with the release
history, host inventory, example configuration and the triage tooling used by
the response team.

Nothing here talks to a live service. Host names are staging-internal names,
client addresses come from the documentation ranges (203.0.113.0/24 and
198.51.100.0/24), and the host keys and example configuration are synthetic.

## Layout

```
./
├── Makefile
├── README.md
├── config/
│   └── checkout.env.example   # placeholders only; no real credentials
├── deploy/
│   ├── known_hosts            # synthetic pinned hosts for the staging stack
│   └── release-history.md     # what changed, when, and the known-good version
├── docs/
│   ├── incident-brief.md      # alert summary and team roles
│   ├── runbook.md             # severity matrix, evidence rules, report contract
│   └── security-notes.md      # approved scanning windows
├── inventory/
│   └── hosts.csv              # host, role, owner, patch date
├── logs/
│   ├── api-02/checkout.log    # application log, JSON lines
│   ├── db-03/postgres.log     # PostgreSQL server log
│   └── edge-01/access.log     # TLS edge access log
└── tools/
    ├── triage.py              # deterministic measurements over the logs
    ├── verify_remediation.py  # validates a staged pool profile
    └── tests/test_triage.py   # unit tests for the triage tool
```

## Commands

Run these from this directory (or with `make -C <this directory> <target>`):

```sh
make triage    # writes build/triage.json and prints a one-screen summary
make test      # runs the triage tool's unit tests
make verify REMEDIATION=/tmp/ir-drill-3f62e1/remediation/pool.env
make clean
```

`make triage` reads only `logs/` and writes only `build/triage.json`.
`make verify` reads only the file named by `REMEDIATION`; it writes nothing.
The repository is a replay fixture, so the logs are the ground truth for
timings and the tools are the ground truth for counts.
