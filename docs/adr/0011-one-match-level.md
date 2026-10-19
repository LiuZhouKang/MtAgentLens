---
status: accepted
date: 2026-10-18
---

# 0011 — One match-level taxonomy: delete the legacy evidence universe

## Context

The `responsibility::evidence` module contained **two parallel universes**:

| Item | Authoritative | Legacy | Difference |
|---|---|---|---|
| `MatchLevel` | `types/evidence_types.rs` (L1Exact/L2Normalized/L3Containment/L4Basename/L5Substring, discriminants 5→1) | `evidence/correlator_legacy.rs` (L1Exact/L2Basename/L3Host/L4Argv0/L5Substring, no discriminants) | different variants, different serde (`PascalCase` vs `snake_case`) |
| `CorrelationEvidence` | `types/evidence_types.rs`, persisted in `correlation_evidence` | `correlator_legacy.rs`, in-memory only | different fields (`agent_id`/`candidate_id` vs typed IDs) |
| `ActionCandidateType` | `types/evidence_types.rs` (8 variants) | `evidence/action_candidate.rs` (6 variants, snake_case) | missing FileDelete/NetworkSend |
| `CandidateDerivationMode`, `SignalClass` | `types/evidence_types.rs` | `evidence/action_candidate.rs` | two more copies |
| `NormalizedArg` | `types/evidence_types.rs` (`Url { host, port }`) | legacy used `Url(String)` | different shapes |

There were also **three ordinal conventions** for the same strength order:

- `types::MatchLevel` discriminant: `L1Exact = 5 … L5Substring = 1` (higher = stronger);
- `graph_pattern_matcher::match_level_ordinal`: `L1Exact = 1 … L5Substring = 5` (higher = weaker);
- `argument_matcher::match_level_ord` (and `confidence::base_score`): explicit 5→1 functions.

The handoff required a design before any deletion, because a naive merge could
silently reinterpret persisted rows.

## Audit (first-hand, before touching anything)

Every consumer of the legacy universe was enumerated from the tree:

- `correlator_legacy::` qualified imports: `graph/graph_pattern_matcher.rs`,
  `graph/graph_simplifier.rs`, `evidence/root_cause.rs`, `evidence/evidence_record.rs`,
  plus one test helper.
- Bare re-export consumers: none outside the deleted modules.
- The runtime imports `evidence::correlator::EvidenceCorrelator` and
  `graph::pattern_matcher::GraphPatternMatcher` **explicitly** — the new ones.
- Each candidate module was then checked for external references:
  `graph_pattern_matcher` (585 lines), `graph_simplifier` (214),
  `root_cause` (297), `evidence_record` (59), `action_candidate` (434):
  **zero** references outside themselves after the legacy correlator is removed.

### Is there data to migrate? No.

- The persisted column is written with `format!("{:?}", ev.match_level)`
  (`writer.rs`), i.e. the *authoritative* enum's `L1Exact … L5Substring`.
- `reader.rs::parse_match_level` reads exactly those strings.
- The legacy `snake_case` values (`l2_basename`, `l3_host`, `l4_argv0`) appear
  nowhere in the repository: no SQL, no JSON fixture, no DB table, no export.
- `PendingArbitration` and `EvidenceRecordNew` are never persisted (and
  `EvidenceRecordNew` is never constructed at all).

So the "migration" is a no-op: no row, fixture or wire message carries a legacy
value. Had any existed, the mapping table below is the interpretation to use.

## Mapping table (for any legacy value encountered outside this repository)

| Legacy | Authoritative | Rationale | Score change |
|---|---|---|---|
| `L1Exact` (0.90) | `L1Exact` (0.90) | exact equality | none |
| `L2Basename` (0.70) | `L4Basename` (0.50) | `file_name` equality; the authoritative taxonomy reserves L2 for normalization | 0.70 → 0.50 |
| `L3Host` (0.75) | `L2Normalized` (0.75) | same host, port may differ — `compare_urls` L2 | none |
| `L4Argv0` (0.65) | `L4Basename` (0.50) | command-name (basename) equality; the authoritative comparator would score an *exact* command equality as `L1Exact`, so prefer re-running that comparator over translating | 0.65 → 0.50 |
| `L5Substring` (0.40) | `L5Substring` (0.35) | substring / longest-common-substring | 0.40 → 0.35 |

## Decision

1. **Delete** `evidence/correlator_legacy.rs`, `evidence/action_candidate.rs`,
   `evidence/evidence_record.rs`, `evidence/root_cause.rs`,
   `graph/graph_pattern_matcher.rs`, `graph/graph_simplifier.rs` and every
   re-export of them (3,753 lines). They are dead code: no runtime caller, no
   persisted data, no external consumer.
2. **Keep** `types::MatchLevel` as the only taxonomy, with its existing
   discriminants and derived `Ord`.
3. **Relocate the one live helper** `edge_has_non_temporal_support` to
   `types/support_types.rs`, next to `CausalEdge`, and point the contract test
   at it. The assertion is unchanged.
4. **Rename** the `NewEvidenceCorrelator` alias back to `EvidenceCorrelator`
   now that the legacy one no longer exists — one name, one type.
5. **Gate it**: `collector/tests/single_source_constants.rs` asserts exactly one
   `enum MatchLevel` and exactly one `AGENT_COMMS` definition in
   `collector/src`. A second copy fails the build.

## Consequences

- 51 tests that exercised the dead universe disappear with it
  (1069 → 1018 passing). They were testing code no product path reaches.
- `graph_pattern_matcher`'s 19 `required_edge_types` concern (handoff §5.8)
  loses its dead half; the live matcher is `graph/pattern_matcher.rs` and is
  audited separately.
- External code that imported the legacy names from `mtagentlens` breaks. This
  is a library in an application repository; the audit found no such consumer,
  and keeping a "backward compat" surface with no consumers is how the 13
  never-compiled files survived.

## Confirmation

- `cargo check --all-targets` after deletion: no references remain.
- `cargo test`: 1018 passed / 0 failed / 3 ignored.
- `single_source_constants` passes and fails if a second `enum MatchLevel` or
  `AGENT_COMMS` definition is added.
- `docs/prepare/09-后端代码深度详解.md` no longer describes the deleted file.
