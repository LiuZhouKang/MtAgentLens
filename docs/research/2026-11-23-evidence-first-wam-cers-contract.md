# MtAgentLens · Evidence-First WAM/CERS Design & Acceptance Contract (v0.1)

> **Status: research contract and validated Python *reference oracle*, not a validated production algorithm.**  
> Audit basis: `LiuZhouKang/MtAgentLens@350efaeb3c531ab1195d001c621be69089f36e78`; 2026-11-23.  
> Source basis: repository source and current evaluation documents; `初稿.pdf` is a **historical draft**, not a substitute for the newer 93-scenario frozen experiment.  
> Deployment: passive outside-of-agent monitoring first; no unconditional chain-of-thought capture; single Agent is a special case; never merge independent conversations merely because they share a PID/TLS socket.

## 0. What is decided and what is not

**Stable research problem**: multi-agent execution attribution under incomplete, heterogeneous and ambiguously scoped runtime evidence. System contribution must be tested against independent execution outcomes and a strong same-information budget baseline, not inferred from having a responsibility graph.

**Stable architectural contract**: M0 raw witness ledger → M1 versioned identity boundary → M2 WAM (witness-bounded association candidates) → M3 CERS (evidence-constrained responsibility explanations) → M4 authorization-aware detection/abstention → M5 deterministic replay and evaluation.

**Not frozen**: M1 resolver implementation, WAM ranking/pruning, CERS algorithms, PBC value, Observer thresholds, capture coverage, end-to-end latency, Root/Entry/Chain accuracy, or an assertion of SOTA superiority.

**Preserve**: frozen `main` and historical GT/metrics; never silently overwrite benchmarks. Existing PR #2–#6 are Draft and need CI/Linux tests and old-vs-new migration reports before any merge.

## 1. Failure modes confirmed in code review

| Area | Exact code | Observed property and implication |
|---|---|---|
| Unproven ancestry | `signals/signal_snapshot.rs` prior to PR #2 | global parent-map fallback can infer connections across disconnected trees; must abstain when lineage is missing |
| TLS ownership | `signals/signal_graph.rs` / `signal_snapshot.rs` prior to PR #3 | last-writer ownership may project old messages to another process |
| Process identity | `runtime/routing.rs::resolve_process_id` prior to PR #6 | missing start timestamp chooses first PID match in `HashMap`, not a proven generation |
| Confidence idempotence | `identity/resolver.rs` prior to PR #5 | repeated sole-owner TLS can be reinterpreted as shared, inflating confidence |
| Capture quality | `replay/checkpoint.rs::quality_status` prior to PR #4 | `decode_status` loss vocabulary larger than quality gate's checked values |
| WAM candidate semantics | `evidence/correlator.rs:442–641` | batch retains one best intent/action pair, computes candidates with scan-order-dependent competing counts; `Authoritative` is phase metadata not a causal proof |
| Stream vs. replay | `evidence/correlator.rs:270–400`, `:442–641` | streaming can produce multiple links in bounded buffers, batch chooses one per action and uses zero time penalty |
| OS Effect | `bpftools/process/process.bpf.c:894–935` | syscall dispatcher lacks read/write/sendmsg/openat2 coverage; successful open ≠ bytes read, successful connect ≠ data transferred |
| Evidence release | `compare/results/README.md` | per-scenario data and metrics intentionally separate from Git; a public clone alone cannot reproduce reported numbers |

*The code paths above are source observations. Their prevalence and performance impact in real runs remain unmeasured.*

## 2. Formal semantics: do not promote correlations into causes

For each event `e` retain:

`(event_id, producer_id, source_integrity, observed_at_ns, clock_domain, process_generation, payload_hash, capture_completeness, event_version)`.

For each invocation `i` and action `a` define the **relation set**:

`R(i,a) ∈ { MUST, MAY, NO, INDETERMINATE }`.

- **MUST**: a precise invocation→action bridge **actually attested by a separately authenticated/validated instrumentation producer**, both endpoints observed with relevant capture healthy, with no trusted contradictory evidence. It is a statement under these observation/trust assumptions, not metaphysical causal proof.
- **MAY**: exact full-generation process match, fully witnessed process lineage, native tool-call reference, or untrusted/derived bridge supports a candidate. None of those alone proves *which* logical Agent issued it. A rank is only a tie-break within a candidate set.
- **NO**: an explicit trusted negative witness excludes the *exact* pair under a documented closed-world capture contract. No match, missing event, or different PID alone is **not** NO.
- **INDETERMINATE**: no adequate evidence, missing identity generation, contradictory trusted witnesses, or non-comparable evidence cannot support a unique claim.

An action-level decision has `selected_intent_id` only for an **uncontested single MUST**. If several MAY candidates exist, output them all with their evidence and do **not** force Top-1. If two distinct MUST bridges claim the same action, mark it `INDETERMINATE` until instrumentation proves how multi-owner operations should be represented.

No number of duplicate, repeated, temporally proximate, or same-resource observations can upgrade MAY to MUST without new *kind* of evidence. The rank is **not calibrated confidence**. If witness capture degrades, claims must not become stronger.

### Crucial trust distinction

`IntentAuthoritySource::RequestHistoryAssistantToolUse`, conversation IDs, model-generated tool-call IDs, and content-bearing files are **protocol assertions**, not authorization. Even an observed model request is not a trusted grant of access. `Trusted authorization` must originate from a separately validated user/controller boundary. In passive-only installations where unique dispatcher bridges are absent, abstention is an expected correct result, not an algorithm failure.

The reference implementation deliberately requires an additional `trusted_ingest=True` capability on an observed allowlisted bridge. This flag is **not accepted from JSON** by the fixture CLI, because a forged `producer` string must not become an attestation. Production Rust must validate event provenance at ingestion, not trust a user-controlled field.

## 3. Reference-oracle implementation delivered in this PR

`research/wam_reference/model.py`:

- Explicit `ProcessKey(pid, start_time_ns, namespace_id)`; PID-only never proves a generation.
- Immutable intent/action/positive bridge/negative witness evidence types.
- Four relation statuses with a clear absence vs. negative-evidence distinction.
- Full-generation observed parent paths (missing edges never stitched using global ancestors).
- Deterministic scoring and canonical JSON independent of input permutations.
- Deduplicated evidence references (re-observation cannot increase rank).
- Multi-owner contradictions and degraded capture trigger abstention/downgrading.
- Reconstructed action-level candidate sets with no fabricated unique winner.

`research/wam_reference/tests/test_model.py`: synthetic regression and permutation tests. `cli.py`: illustrative non-privileged JSON fixture renderer; its untrusted input cannot self-assert a MUST. The default namespace, source tier,
and capture health are unknown unless explicitly supported.

Run (repository root):

```bash
python3 -m unittest discover -s research/wam_reference/tests -v
python3 -m research.wam_reference.cli --example
```

The tool is an **executable specification**, not a high-throughput production matcher. It uses `O(|I|·|A|)` pair enumeration, with no index/latency guarantee; it has not observed a real Agent or measured benchmark accuracy. Promotion requires a separately reviewed Rust implementation and differential tests.

## 4. M0/M1/M2/M3 interface contracts for Rust implementation

| Boundary | Required inputs | Required outputs | Unsafe shortcut to forbid |
|---|---|---|---|
| M0→M1 | Raw process/TLS/IPC evidence + generation + capture status | `IdentityCandidateSet{process,agent?,source,valid_interval,conflicts}` | PID-only `HashMap` first match; shared TLS implies single Agent |
| M1→M2 | explicit identity candidate sets + delegations + provenance | `InvocationFact`, `ExecutionFact`, `EffectFact`, `BridgeEvidence` | deriving authorization from model text, using UI session as authenticated principal |
| M2→M3 | candidates with status, references and conflict-set IDs | typed edge candidates (`Control`, `Content`, `Execution`, `Effect`) | `PhaseIndicator::Authoritative` interpreted as proven cause |
| M3→M4 | root/entry/chain **candidates**, negative controls, coverage | evidence-backed decisions `Unsafe/Safe/Uncertain` | counting graph depth, PBC edges, or high score as proof of root cause |
| M4→M5 | decision, raw hashes, config, model, scope, quality | replay manifest and migration matrix | report current paper results from stale prior experiments |

### M0: OS Effect Witness ledger (required, not yet implemented)

Maintain `fd` and socket lifecycle keyed by **process generation + descriptor generation**. Observe `open/openat/openat2`, actual returned `read/pread/readv` byte counts, `write/send/sendmsg/sendmmsg` return sizes, `dup/close`, connected peer changes, and error/pending states. A `connect` success proves a connection result; no body contents or sensitive exfiltration without separately observed data-flow witness. Avoid broad blanket probes without measured overhead and privilege safety.

### M1: identity must stay versioned and abstention-aware

A single process can execute several conversations concurrently. Model `ProcessActor`, `LogicalAgent`, `Conversation`, `Dispatch`, `Session`, and `Delegation` as **distinct entities** with validity intervals. Shared transport is association evidence only. When no unique session/dispatch owner is supported, preserve a candidate set or `INDETERMINATE` and do not retroactively overwrite already observed message provenance.

### M2: WAM production replacement must be phased

1. Add typed evidence witness DTOs and stable association candidate sets **without changing the legacy scorer**.
2. Differentially compare Python oracle vs. Rust on identical synthetic fixtures (same canonical sorting, same missing-data semantics).
3. Introduce bounded indexes keyed by generation / verified bridge / native call reference. Measure peak RSS and candidate explosion on realistic 66×18,520 style workloads.
4. Run legacy/new candidates in shadow mode; persist both and their disagreements, never silently rewrite frozen evidence.
5. Wire new candidate statuses into graph only after downstream code handles MAY, NO and INDETERMINATE; remove misleading `Authoritative→causally proven` wording.

### M3: CERS responsibility contracts

A responsibility explanation consists of a typed, time-valid *evidence path* and an explicit **claim class**. Keep at least:

- `Control`: trusted instruction/delegation or authorization event.
- `Content`: an agent observed or consumed a lower-trust artifact with source identification.
- `Execution`: invocation-to-process action relationship with support status.
- `Effect`: observed OS state/bytes/result, not merely an attempted syscall.

`Root` = candidate originator of a specified suspicious behavior under chosen trace semantics; `Entry` = candidate trust-boundary introduction; `Chain` = candidate ordered explanation. These are three distinct scoring targets. An observed path is *not* automatically proof that removing its root would prevent the downstream outcome; source deletion can test graph dependence but is not a causal intervention. Safe, sandboxed controlled interventions and independent state oracles are required for claims about counterfactual effects.

## 5. Reproducible experimental design (frozen cohort, no fabricated gains)

- Preserve historical 86-scenario paper draft separately from current 93-scenario handoff (58 attacks, 35 benign); never compare different denominators as a clean improvement/degradation.
- Publish a machine-readable asset manifest: commit, branch, dirty flag, kernel/arch, BPF attachment set, captured source quality, clock-domain mapping, scenario IDs/GT freeze, runtime and model version, annotation version, script hash, per-run artifact hash.
- Report identity **coverage, assigned-pair precision, mistaken merge rate, unknown**, stratified by shared process, shared TLS, PID reuse, delegation and concurrency.
- Report WAM candidate recall / precision by status, `MUST` precision, MAY-set recall, top-k only on attributable cases, mean candidate set size, abstention calibration, and permutation/stream replay parity.
- Report CERS `Root@1`, `Entry@1`, `Chain` edge/path F1, selected abstention rate and negative-control false attribution. Fixed three-candidate `Root@3=100%` is a coverage metric, not a solved ranking problem.
- Compare **identical evidence budgets**: PID-only, native tool-call ID-only, TLS-only, temporal-nearest, legacy batch correlator, traced spans where available, and evidence-aware oracle/Rust candidate mechanism. Include independent cross-agent causality references and experiment blinding.
- Recompute `Safe↔Uncertain↔Unsafe` migration, especially legitimate benign abstentions induced by stricter evidence gates. Privacy/redaction must preserve evaluable provenance without exposing secrets.
- Measure eBPF loss by reason and source, CPU, p95/p99 latency and peak RSS under benign and adversarial loads. Past planning targets (OS ≤200ms, semantic ≤800ms, team ≤1.5s, CPU ≤5%, PSS ≤400MiB) are **targets, not demonstrated results**.

### Paper release gate

**Do not state** "causal root cause localization is solved" or that this oracle has production TPR/FPR. The currently documented Root@1 = `5/22` (22.7%) is the candid baseline, and current 93-run data is not included in a public repository clone. New claims must cite frozen per-run evidence, independent ground truth and reproducible scripts.

## 6. Differential acceptance / merge order

**Gate A: conservative fixes** — run Rust targeted tests for PR #2–#6 independently; merge only after static review, full `./scripts/verify.sh`, Linux probe tests and frozen-run migration reports. PR #4 must separately test legitimate binary TLS (text semantic incompleteness is not byte loss).

**Gate B: reference oracle** — CI invokes `python3 -m unittest discover -s research/wam_reference/tests`; require the actual test count to be nonzero and fixed, plus deterministic permutation tests. This PR is independent and may merge as a research-only artifact after this gate.

**Gate C: Rust WAM shadow** — exact Python/Rust parity on a machine-generated fixture corpus; no hard production switch until legacy comparison and memory budgets pass.

**Gate D: CERS causal evaluation** — independently annotate Root / Entry / Chain, construct difficult benign and negative controls, and report whether sparse witnesses warrant abstention. Avoid tautological ground truth from the same graph construction rules.

**Gate E: integrated paper release** — only once M0–M4 agree on `Observed/Derived/Hypothesized` and all data and code SHA manifests are shared or validly access-controlled.

## 7. Prior art and novelty boundary

The following externally published work overlaps with the project and must be accurately cited and experimentally compared; they do **not** establish that MtAgentLens is already more effective:

- **AgentSight (2025)**: passive, eBPF-based boundary tracing for LLM agents already couples LLM traffic and OS activities. Thus "we first capture Agent→OS events without SDK changes" is not defensible as the unique novelty. DOI / preprint: <https://arxiv.org/abs/2508.02736>.
- **OpenTelemetry GenAI (ongoing)**: spans, agent conversation identifiers and tool operations have an established schema. Evaluation must compare an instrumented-span baseline *where the adapter is available*, while preserving the agent-modification-free scope of MtAgentLens. <https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md>.
- **TRACE (2026)**: evidence-based and tamper-evident multi-agent accountability has explicit provenance/commitment prior work. Merkle logs alone cannot be claimed as a new core research contribution. <https://doi.org/10.1109/BigDataSecurity69476.2026.00021>.
- **Responsible Agentic AI Requires Explicit Provenance (2026)**: formal attribution and responsibility structures exist in contemporary literature. MtAgentLens must prove specific operational advantages under shared-process, incomplete-capture and ambiguous identity conditions. <https://arxiv.org/abs/2605.17169>.

**Candidate differentiator to validate**: independently verifiable, *abstention-aware* cross-layer responsibility candidates in **passive multi-agent execution**, under process/session sharing and partial observability, with a direct comparison to the strongest observation-equivalent baseline. This is a hypothesis and experimental agenda, not an already proven best-paper result.

## 8. Execution handoff

### Commands requiring a full local Linux checkout

```bash
# Focused independent Python reference test (no root/network)
python3 -m unittest discover -s research/wam_reference/tests -v
python3 -m research.wam_reference.cli --example

# Existing project's complete Rust/eBPF/frontend gate (not run in this review environment)
./scripts/verify.sh

# Existing Linux eBPF integration, requires the specified privileges and host prerequisites
sudo python3 bpftools/tests/test_capture_integration.py
```

### Required report from the local test executor

`gate_results.json` should record for each command: start/end, exit code, stdout/stderr hashes, git SHA, environment fingerprint, dependency and toolchain versions, test count, and any unavailable hardware/privileges. Failed gates stay red. A merge or a paper metric update must be a separately reviewed action, not inferred from an open PR.

**Current delivered status:** synthetic Python oracle tests pass locally; **no Rust compilation, no Linux eBPF probe, no real-agent benchmark and no cross-PR integration has been executed** as part of this delivery.
