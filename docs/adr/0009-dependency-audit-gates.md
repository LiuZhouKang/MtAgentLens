---
status: accepted
date: 2026-10-18
---

# 0009 — Dependency audit gates: accepted advisories carry a reason, and budgets only shrink

## Context

The OpenSSF self-assessment recorded two gaps that no gate covered:

- **No automated dependency audit.** `cargo audit` and `npm audit` were never
  run, so a known-vulnerable crate or package would ship with a green badge.
- **A budget is only as honest as its exit condition.** Every other budget in
  this repository (`scripts/verify.sh`) may only decrease. A dependency
  allow-list is especially prone to becoming a blanket disable: an entry added
  under deadline pressure stays forever, long after the dependency is patched.

There is a second constraint: `scripts/verify.sh` is offline and deterministic
(ADR 0001). `cargo audit` needs the RustSec advisory database and `npm audit`
needs the npm registry. Folding either into `verify.sh` would make a green gate
depend on network state, and a gate that flapped would eventually be disabled —
strictly worse than no gate.

## Decision

1. **Two checks, not one gate.** `python3 scripts/audit_deps.py all` is a
   separate command because it needs the network. `verify.sh` remains offline
   and gains a 14th gate that validates the *budget file itself* offline.
2. **Every accepted advisory carries a reason and an identity.**
   `scripts/audit-budget.json` holds `cargo` entries (`id`) and `npm` entries
   (`package`, `advisory`, `severity`), each with a non-empty `reason`.
   npm entries are keyed by advisory, not by package: a package-level entry
   would silently pre-accept every later advisory for that package, and the
   stale rule could never notice. A vulnerable package with no identifiable
   advisory id fails closed.
3. **Budgets only shrink.** If an accepted advisory no longer appears in the
   live audit, the gate fails and names the stale entry. Removing it is the
   only fix. This is the mechanism that keeps the budget from becoming a
   permanent allow-list.
4. **A missing tool or an unreadable report is a failure, not a skip.** If
   `cargo-audit` is absent, the report lacks `vulnerabilities.list`, npm exits
   with a status outside 0/1, or npm's JSON lacks `auditReportVersion`/
   `metadata` (a mirror without the audit endpoint answers with an error
   object), the gate fails with the relevant output. Reading a missing
   `vulnerabilities` key as "zero advisories" is the exact false green this
   gate exists to prevent.
5. **Only moderate/high/critical npm advisories fail.** Low/info advisories in
   the build toolchain are reported but do not gate; the budget schema rejects
   entries for severities that cannot fail, so no one can pre-accept noise.
6. **npm gates the production dependency set (`--omit=dev`).** The artifact is
   the embedded Next.js export; storybook/vitest/eslint never reach it. Those
   dev-only advisories are recorded as a residual in `docs/capabilities.json`
   rather than becoming a reason wall that would rot. A build-tool compromise is
   a supply-chain problem, handled by the build-host trust boundary, not by an
   advisory count.

## What the first real run found

The gate was not decorative. On its first run it found 13 Rust vulnerabilities
and 6 production npm vulnerabilities:

- The Rust ones came from the unused `anthropic = "0.0.8"` dependency, which
  pulled `reqwest 0.11 → hyper 0.14 → h2 0.3 → rustls 0.21 → rustls-webpki 0.101`.
  Nothing in `src/` referenced the crate, so it was **deleted**; `cargo update`
  then moved `bytes`, `slab`, `h2 0.4`, `rustls 0.23` and `rustls-webpki 0.103`
  to patched releases.
- The npm ones were fixed by patch/minor updates (`next 16.2.9 → 16.4.2`,
  `postcss`, `sharp`, `nanoid`, `source-map-js`, `baseline-browser-mapping`).
  Frontend type-check, lint and 528 unit tests pass afterwards.

Both budgets are therefore **empty**. That is the intended steady state: a
non-empty budget is a visible debt, not a normal condition.

## Consequences

- The audit is network-dependent by nature. Its failure carries the same
  information as any other gate: either upgrade the dependency, or record why
  this exact advisory does not apply — in writing, next to a number that must
  be lowered when it stops applying.
- Local `verify.sh` stays green without network, so contributors can work
  offline; the audit is an explicit command in `CONTRIBUTING.md`.
- The honest status of "no known unfixed vulnerabilities" is *conditionally
  satisfied*: satisfied only after `scripts/audit_deps.py all` runs green on
  the build host before release. There is no CI runner (ADR-0014).

## Confirmation

- `./scripts/verify.sh` gate 14 prints the parsed budget and fails on a missing
  reason.
- `python3 scripts/audit_deps.py all` fails on a live, unbudgeted advisory and
  on a stale budget entry; tests for both behaviours live in
  `scripts/tests/test_audit_deps.py` and run inside verify.sh gate 14.
- `CONTRIBUTING.md` names the exact command, so a release run and a local run
  cannot drift.
