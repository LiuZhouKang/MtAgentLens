---
status: accepted
date: 2026-10-18
---

# 0012 — Graph pattern templates: the matcher could not fire; the gap is now machine-checked

## Context

`graph/pattern_matcher.rs` defines 14 attack templates and
`GraphPatternMatcher::match_graph` runs on every runtime closure
(`runtime.rs`, streaming and authoritative paths). Its results feed
`ClassificationEngine::classify_authoritative` as pattern evidence.

The handoff flagged "19 `required_edge_types` with no producer". The first-hand
audit (constructor sites only, `mod tests` excluded, `pattern_matcher.rs`
excluded because naming a type is not constructing it) found something larger:

| Vocabulary | Produced by the graph builder |
|---|---|
| **Nodes** | `Agent`, `Resource`, `PolicyBoundary` |
| **Edges** | `IntentCausesAction`, `PolicyBoundaryCrossing`, `TaintPropagatesTo` |

Every one of the 14 templates required at least one type outside that set, and
`try_match` rejects a template when any `required_node_types` entry is absent
(`pattern_matcher.rs:57-63`). Therefore **no template could ever match**: the
matcher ran on every closure and returned `[]`. Nothing failed, because an
empty match list is a valid result.

The same audit found the cross-agent gate (`pattern_matcher.rs:85-92`) keyed on
`Delegation | ContextInheritance | TaintPropagation` — three types with no
producer. It also found `DelegationTracker` (judgment/delegation.rs) fully
implemented but never instantiated; the graph builder passes `&[]` for
delegation records with the comment "populated in Phase 2".

## Decision

1. **Do not fake a fix.** Rewriting all templates onto
   `[IntentCausesAction, PolicyBoundaryCrossing]` would make them all fire on
   the same graph — 14 names for one signal, which is worse than zero matches.
   Producing `Delegation`/`ContextInheritance`/`CapabilityUse` edges from the
   same action would duplicate `IntentCausesAction` under four names.
2. **Declare the gap, explicitly and structurally.** `docs/capabilities.json`
   gains:
   - `unbacked_graph_types.node` / `.edge`: the types the graph builder does
     not construct, with a reason;
   - `inert_graph_templates`: the template ids that cannot fire today.
3. **Make the gap shrink-only.** `collector/tests/graph_pattern_contract.rs`
   scans constructor sites in `collector/src`, instantiates
   `default_templates()`, and asserts:
   - every requirement is produced **or** declared (no silent unbacked);
   - every declared unbacked type is still unproduced (stale declarations fail);
   - the computed inert-template set equals the declared one exactly.
   The inertness computation **mirrors the matcher's own reachability rule**,
   not just "some required type is unproduced": `min_matches` counts distinct
   required edge types (so `min_matches > required_edge_types.len()` is
   unreachable), and `min_distinct_agents >= 2` also needs a produced
   cross-agent edge type. Auditing against that rule found two structurally
   unreachable `min_matches` values (`reconnaissance` 3 with one edge type,
   `multi_agent_collusion` 3 with two); both were lowered to the maximum the
   matcher can satisfy. A test now rejects that class directly.
4. **Accept both names in the cross-agent gate.** `Delegation |
   AgentDelegatesTo | ContextInheritance | ContextFlowsFrom | TaintPropagation |
   TaintPropagatesTo`. When a producer lands under either spelling, the gate
   works without a second edit.
5. **Report only the types the template asked for.** A match used to list every
   node and edge in the graph as evidence (`matched_node_ids`/`matched_edge_ids`
   came from `all_nodes()`/`all_edges()`); it now filters by the template's
   required types.

## What restoring detection requires (design precondition)

The matcher currently compares only *type sets*. Even with producers, the
templates are not distinguishable: `data_exfiltration`,
`persistence_backdoor` and `indirect_prompt_injection_tool_abuse` all reduce to
"agent acted on a resource, crossing a policy boundary". A real fix needs:

1. **Edge producers**: wire `DelegationTracker` into the runtime and emit
   `AgentDelegatesTo`; emit `ContextFlowsFrom` when a child agent inherits a
   conversation; emit `CapabilityUse` (or use `IntentCausesAction`) only if it
   adds information.
2. **Resource-kind-aware matching**: `NormalizedResource::{File, Network}` and
   the action type already distinguish read/write/send/delete; the template
   predicate must use them.
3. **Node producers**: `ToolCall`/`Credential`/`Context` are model concepts,
   not observable node kinds in the current graph. Either derive them
   (tool call = action + intent tool name; credential = resource path pattern)
   or keep them out of the templates.

Until then the templates stay inert and declared. This ADR is the entry point
for that work; the contract test is the finish line.

## Update (2026-10-18, later): detection restored

The gap above was closed rather than left declared:

- **`AgentDelegatesTo` / `ContextFlowsFrom` producers.** Cross-agent
  orchestration (an orchestration-tool intent by agent A whose action is by
  agent B) now emits both edges in `graph_builder.rs`, with confidence graded
  by the delegation policy (Observed / Ambiguous / Unproven). The context edge
  is separate because the handoff carries context; it is not a second meaning
  for the delegation edge.
- **`DelegationTracker` wired into `ResponsibilityRuntime`.** A process spawn
  whose child later resolves to a different agent produces a
  `DelegationObservation`; it is persisted as a typed `DelegationRecord` and
  fed to the builder, so the policy gate can return Observed instead of only
  Ambiguous. The tracker's dedup pruning bug (it removed the pair as soon as
  the spawn origin was consumed, re-emitting the same delegation) is fixed.
- **Credential resources.** `action_to_resource` classifies credential-shaped
  paths (`id_rsa`, `.env`, `.aws/credentials`, …) as
  `NormalizedResource::Credential`, activating the credential branches that
  already existed in scoring, the rule engine and the claim builder.
- **Resource-kind discriminator.** `GraphPatternTemplate` gained
  `required_resource_kinds` (`File`/`Network`/`Process`/`ToolCall`/`Credential`),
  `required_edge_target_kinds` (e.g. the boundary crossing in an exfiltration
  must target the *network* resource, not a benign SSH read), and every
  template was rewritten onto the produced vocabulary.
- **Connected evidence.** The matcher now requires the required edges to form
  one connected subgraph (union-find over endpoints), the required node types
  and resource kinds to be carried by nodes on those edges, and the distinct
  agents to be counted among those endpoints. A file read by one agent and a
  network crossing by an unrelated agent no longer combine into
  `data_exfiltration`.
- **Delegation is data-dependent.** `AgentDelegatesTo`/`ContextFlowsFrom` are
  produced only when an orchestration-tool intent correlates with another
  agent's action (the correlator requires the action process to descend from
  the intent process). Templates that require those edges are reachable, but
  a standalone orchestration intent without a correlated action produces no
  delegation edge. This is recorded here rather than claimed as universal.
- `inert_graph_templates` is now **empty**; the contract still computes the
  set exactly and fails if a template becomes unreachable again.
  `unbacked_graph_types` now lists only synonymous/conceptual types with no
  builder path; no template uses them.

Confirmation: `graph_pattern_contract` (5 tests), matcher behaviour tests
(file-only is not exfiltration; one agent is not a trust-boundary crossing;
credential theft needs a Credential resource), graph-builder delegation and
credential tests, and the full suite (1034 tests).

---

## Update (2026-10-19, P1.2 sample): verdicts are persisted; reconnaissance tightened

Two gaps surfaced when the templates were measured on a fresh 12-scenario sample:

1. **The matcher's verdicts never reached the database.** `AlertBuilder` built
   the engine-only `EngineSection` with `pattern_verdicts: vec![]`, so a fired
   template and an empty match were indistinguishable. The verdicts now live on
   `ClassificationResult.pattern_verdicts` and are copied into the alert's
   engine section. An empty list now means "no match".
2. **`reconnaissance` fired on any file action.** It required only
   `Agent --IntentCausesAction--> File`, which is every read; it matched 5/6
   benign scenarios. It now also requires `PolicyBoundaryCrossing` and
   `min_matches: 2`. On the same sample: benign 5/6 → **0/6**, attack 6/6 → 2/6.
   This also removed a spurious `Unsafe` on `delegation-chain`, whose score was
   driven by the over-broad template — the honest trade is a narrower, precise
   signal.

Occurrence-based matching (counting repeated probing) is still not implemented;
the placeholder `min_matches` semantics are unchanged.

## Confirmation

- `cargo test --test graph_pattern_contract`: 5 tests, including exact-set
  equality for inert templates.
- `docs/capabilities.json` names every unbacked type and every inert template.
- The runtime continues to call the matcher; it now has a documented reason to
  return an empty list, and any new producer must update the manifest or the
  build fails.
