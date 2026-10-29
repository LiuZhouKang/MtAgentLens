# ADR 0002 — Out-of-process plugins

- **Status:** accepted
- **Date:** 2025-12-02
- **Deciders:** platform team, firmware team
- **Supersedes:** none

## Context

Three teams contribute payload parsers for their own sensor families. Parsers
change on a different cadence from the core and have had two incidents in the
prototype: one parser looped forever on a truncated payload and blocked the
core pool, and another allocated without bound on a malformed length field and
took the process down. Both were ordinary parser bugs, but the blast radius
was the whole ingest path.

A parser only needs the payload bytes and a few envelope fields. It does not
need the store, the query API, the network, or the configuration secrets.

## Decision

Run each parser as a separate supervised child process. The core and the
plugin speak a length-prefixed protocol over a Unix socket:

- request: `version(1) | capability_mask(4) | payload_len(4) | payload`
- response: `version(1) | status(1) | record_len(4) | record`

The plugin declares a capability manifest at start-up (which channels and
which envelope fields it may see). The supervisor enforces:

- a memory ceiling of `FERNLIGHT_PLUGIN_MEMORY_MB` per child (cgroup or
  `setrlimit`, whichever the platform supports);
- a 2 s per-request timeout;
- restart with exponential backoff after a crash;
- no inheritance of the core's environment; the child receives only the
  manifest and the plugin directory.

If a plugin crashes or times out, the core records `parser=identity` for the
affected events using the identity parser and continues. Plugin output is
validated against the same record schema as core output.

## Consequences

- A parser bug can no longer stall or crash the ingest path.
- IPC adds a copy per batch and a context switch per request; measured cost is
  about 4% of core CPU at the design batch size (64 KiB). Accepted.
- The protocol must be versioned and tested across a process boundary; we
  keep a conformance test suite with recorded request/response fixtures.
- Debugging a plugin requires attaching to a child process, not the main
  binary; the supervisor exposes child pids and recent stderr on `/v1/health`.
- Capability enforcement is a manifest plus field filtering, not a hard
  sandbox; a plugin that is given a field can leak it. We document this limit
  rather than claim isolation we do not have.

## Alternatives considered

- **In-process dynamic libraries.** Fastest, but a parser crash is a core
  crash and memory limits cannot be enforced per parser. Rejected.
- **WebAssembly runtime.** Strong sandbox and good startup cost, but adds a
  large dependency and a second toolchain for parser authors. Rejected for
  now; the protocol boundary leaves room to move to WASM later.
- **Separate services over TCP.** Same isolation, but adds another listener,
  authentication surface and deployment unit. Rejected for a single-binary
  product.
