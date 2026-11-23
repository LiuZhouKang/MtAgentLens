# ADR-0015: Evidence-bounded identity, action association and responsibility claims

Status: **PROPOSED — research contract; not deployed in the Rust production path**
Date: 2026-11-23
Base: `main@350efaeb3c531ab1195d001c621be69089f36e78`

## Decision

The unique overarching research direction is **Evidence-First Causal Reconstruction**.
This ADR freezes evidence semantics, *not* a particular correlation algorithm, detection
threshold, claimed accuracy or an assertion of causal identification.

The architecture follows:

M0 trusted raw-witness ledger → M1 versioned actor/session/delegation identity →
M2 WAM typed candidate associations → M3 CERS responsibility hypotheses →
M4 authorization-aware decision/abstention → M5 frozen replay and evaluation.

The passive operating mode remains a primary design constraint: the system may not
assume every agent has instrumented in-process spans or a dispatch token.
Single-agent executions are a special case of the same framework. No unrestricted
capture of private chain-of-thought is required.

## Authority, evidence and execution are different concepts

- `Observed`: an event directly emitted by a named, validated producer and bound
  to a capture-quality record, exact resource and process generation when available.
- `Derived`: a result supported by an auditable deterministic rule and input references.
- `Hypothesized`: an association based on time, arguments, process lineage or similar
  evidence; it is not a unique causal identity.
- `Authorized`: an independent trusted user/controller authorization scope;
  a conversation ID, tool-call ID or model-generated text is *not* authorization.
- `Effect`: the actual OS-level result and measured state/byte effect. Successful
  `open` is not proof of bytes read; successful `connect` is not proof of egress payload.

Every positive witness carries `(event_id, producer_id, integrity_class,
clock_domain, process_generation, capture_quality, evidence_refs, rule_version)`.
A missing field remains unknown; none may be populated by searching an unrelated
process tree or using a last-writer TLS owner as a retroactive per-message fact.

## M2 WAM relation contract

For invocation `i` and action `a`, output:

`R(i,a) ∈ {MUST, MAY, NO, INDETERMINATE}`.

- `MUST`: an uncontested precise invocation-to-action bridge from a **validated**
  trusted execution producer, with observed endpoints and healthy relevant capture.
  This conclusion is conditional on the explicit producer-trust assumptions.
- `MAY`: one or more association signals (same full-generation process, verified
  ancestry, native tool-call ID, resource/temporal match, non-attested bridge).
  These cannot yield a unique owner merely by accumulating more weak matches.
- `NO`: a specific observed and independently trusted exclusion under a defined
  closed-world contract; absence of an event is not a negative witness.
- `INDETERMINATE`: insufficient or contradictory evidence, including conflicting
  direct witnesses. This is a valid output, not a failure to force Top-1.

A stream/batch algorithm can rank MAY candidates for *review*, but cannot label
the top candidate causally proven. `PhaseIndicator::Authoritative` is a
post-hoc processing phase, not a truth or authorization certificate.
Duplicate evidence cannot increase strength. Missing evidence cannot strengthen
conclusions. HashMap iteration and input event order must not determine identities.

## M1 identity boundary

Maintain separate entities for ProcessGeneration, LogicalAgent, Conversation,
Invocation/Dispatch, TLS/IPC Session, Delegation and Observed Effect. A process
may serve multiple conversations and a TLS session may have multiple participants.
A shared process tree is an execution-context link, not proof of instruction origin.

Unknown generations, incomplete lineage, shared connection ambiguity and
delegation conflicts should return candidate sets and abstention. Never silently
choose an instance solely from a PID.

## M3 CERS claim classes

The responsibility graph must distinguish `Control`, `Content`, `Execution`,
`Effect` and `Authorization` relations, each with evidence tier and provenance.
Root, Entry and Chain are separate evaluation targets with separate references.

Witness-supported observational paths are not necessarily causal counterfactuals:
removing an edge changes a graph explanation but does not establish what would
have happened in the world. Real causal effects require safe controlled
interventions and independently measured state outcomes.

## Implementation gates

1. Preserve prior experimental evidence and all Draft fixes PR #2–#7; do not merge
   without review and tests. Publish old-vs-new migration matrices.
2. Implement a standalone, Python-standard-library M2 reference oracle with synthetic
   invariants: source forgery, PID reuse, TLS sharing, parent-gap, duplicate evidence,
   contradictory witnesses, partial capture, 100-order permutations, abstention.
   The 2026-11-23 local research bundle contains the initial tested reference.
3. Add a shadow Rust implementation with typed candidate sets and source validation
   at ingestion. Differential test it against the oracle on identical fixtures.
4. Measure memory and latency against the legacy batch correlator on high-volume
   66-intent/18k-action-scale workloads; no unbounded Cartesian production path.
5. Re-evaluate identity coverage, false merges, MUST precision, MAY-set recall,
   abstention, Root/Entry/Chain, benign false attribution and observer dependence
   on independent truth. Separately run Linux/eBPF integration and `./scripts/verify.sh`.
6. Freeze code SHA, GT, capture quality, clocks, producer versions, per-run artifacts
   and script hashes before changing any paper number. The current 93-run raw
   data is not fully included in the public Git tree.

## Non-goals / novelty qualification

Do not claim first SDK-free eBPF LLM-agent monitoring: AgentSight (2025) already
demonstrates system-level boundary tracing. Do not claim first agent provenance:
contemporary multi-agent provenance and tamper-evident auditing prior art exists.
Our scientific hypothesis to test is **selective, abstention-aware, externally
verifiable responsibility reconstruction under passive partial observability**.
It is not yet established as a superior method.

Relevant prior art:
- AgentSight: https://arxiv.org/abs/2508.02736
- OTel GenAI agent spans: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- TRACE: https://doi.org/10.1109/BigDataSecurity69476.2026.00021
- Explicit Agentic Provenance: https://arxiv.org/abs/2605.17169

## Verification status

This ADR is a research design decision. The external synthetic Python oracle
passed 23/23 unit/property tests on 2026-11-23. **No Rust build, eBPF probe run,
93-scenario replay, measured detection gain, root-cause gain or production
performance claim follows from those tests.**