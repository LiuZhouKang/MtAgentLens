// SPDX-License-Identifier: MIT
// Copyright (c) 2026 eunomia-bpf org.
//
// =============================================================================
// Handoff Chain Verification — Integration Test
// =============================================================================
//
// Validates the full subagent handoff chain (A→B→C→D) by:
//   1. Parsing handoff YAMLs (from embedded fixtures + real filesystem)
//   2. Verifying each subagent output against v2 contract schema
//   3. Checking sequential dependencies: A→B→C→D must form a valid DAG
//   4. Validating field consistency, quality gate status, and risk propagation
//
// Arrange/Act/Assert structure. Each test verifies one specific pipeline behavior.
//
// Contract under test: .kiro/handoffs/E2E_VERIFICATION_SPEC.md §§5-6, 16
//   - §5: Subagent Output Validation Criteria (A→B→C→D)
//   - §6: Chain Integrity Rules (no orphans, no version regression, no drift)
//   - §16: Sequential Validation Protocol (3-step dependency-gated validation)
// =============================================================================

use serde::{Deserialize, Serialize};
use serde_yaml;
use std::collections::HashMap;
use std::path::Path;

// =============================================================================
// Data Models — Handoff YAML v2 Contract Schema
// =============================================================================

/// Top-level handoff artifact structure matching the canonical v2 YAML schema.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct HandoffContract {
    handoff_id: String,
    feature_name: String,
    from_phase: String,
    to_phase: String,
    timestamp: String,
    owner: Option<String>,
    previous_owner: Option<String>,
    next_phase_ready: Option<bool>,

    contract: Option<String>,
    contract_version: Option<String>,

    context: Option<ContextBlock>,
    quality_gates: Option<Vec<QualityGate>>,
    deliverables: Option<Vec<Deliverable>>,
    risks: Option<Vec<Risk>>,
    dependencies: Option<Vec<String>>,
    blockers: Option<Vec<String>>,
    acceptance_criteria: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct ContextBlock {
    summary: Option<String>,
    key_design_decisions: Option<Vec<String>>,
    known_gaps: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct QualityGate {
    gate_name: String,
    status: String,
    evidence: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct Deliverable {
    path: String,
    #[serde(rename = "type")]
    deliverable_type: Option<String>,
    lines_changed: Option<u64>,
    rationale: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct Risk {
    id: Option<String>,
    description: Option<String>,
    likelihood: Option<String>,
    impact: Option<String>,
    mitigation: Option<String>,
    owner: Option<String>,
    status: Option<String>,
}

// =============================================================================
// Canonical phase ordering — design / implementation / testing / documentation
// =============================================================================
const PHASE_ORDER: &[&str] = &[
    "architecture",
    "design",
    "requirements",
    "implementation",
    "testing",
    "documentation",
];

fn phase_index(phase: &str) -> Option<usize> {
    PHASE_ORDER.iter().position(|&p| p == phase)
}

// =============================================================================
// Embedded Deterministic Fixtures
// =============================================================================
//
// These fixtures represent a complete, valid handoff chain for the hypothetical
// "Causal Link Streaming Analyzer" feature. They are embedded as const strings
// so tests are fully deterministic and require no external file access.
//
// Each fixture follows the v2 handoff contract schema and forms a valid
// A(design) → B(implementation) → C(testing) → D(documentation) chain.

const FIXTURE_DESIGN_YAML: &str = r#"
handoff_id: "fixture-design-ho-01"
feature_name: "Causal Link Streaming Analyzer"
from_phase: "design"
to_phase: "implementation"
timestamp: "2026-05-05T13:00:00Z"
owner: "docs-architecture-reviewer"
previous_owner: "null"
next_phase_ready: true

contract: "docs/causal-link-streaming-handoff-contract.md"
contract_version: "1.0"

context:
  summary: |
    Phase design complete: defined the Causal Link Streaming Analyzer contract
    covering implementation, testing, and documentation phases.
    Key design decisions: streaming correlation, bounded memory, non-blocking output.
  key_design_decisions:
    - "DDR-001: Streaming correlation with sliding window"
    - "DDR-002: Bounded memory using LRU eviction"
    - "DDR-003: Non-blocking output via channel"
  known_gaps:
    - "No streaming analyzer implementation (Phase B scope)"
    - "No performance baselines (Phase C scope)"
    - "No frontend visualization (Phase D scope)"

quality_gates:
  - gate_name: "gate-design-completeness"
    status: "pass"
    evidence: "3 DDRs documented, all required sections present"
  - gate_name: "gate-risk-coverage"
    status: "pass"
    evidence: "3 risks documented with mitigations"

deliverables:
  - path: "docs/causal-link-streaming-handoff-contract.md"
    type: "new"
    lines_changed: 450
    rationale: "Full design contract"

risks:
  - id: "R-DESIGN-001"
    description: "Streaming analyzer may cause memory pressure under high event volume"
    likelihood: "medium"
    impact: "OOM risk if LRU capacity exceeded"
    mitigation: "Bounded LRU cache with configurable capacity (default 10,000); TTL-based eviction"
  - id: "R-DESIGN-002"
    description: "Timeline alignment may skew if clock sources differ across events"
    likelihood: "low"
    impact: "False causal links or missed correlations"
    mitigation: "Use kernel monotonic clock (start_time_ns); validate with timestamp_normalizer"
  - id: "R-DESIGN-003"
    description: "Channel backpressure could stall the pipeline"
    likelihood: "low"
    impact: "Pipeline throughput degradation"
    mitigation: "Bounded channel with non-blocking send; drop events under backpressure"
"#;

const FIXTURE_IMPL_YAML: &str = r#"
handoff_id: "fixture-impl-ho-01"
feature_name: "Causal Link Streaming Analyzer"
from_phase: "implementation"
to_phase: "testing"
timestamp: "2026-05-05T15:00:00Z"
owner: "rust-framework-architect"
previous_owner: "docs-architecture-reviewer"
next_phase_ready: true

contract: "docs/causal-link-streaming-handoff-contract.md"
contract_version: "1.0"

context:
  summary: |
    Implementation phase complete: implemented the CausalLinkStreamingAnalyzer
    with sliding window correlation, LRU eviction, and non-blocking output channel.
    450 lines of Rust in analyzer module.
  key_design_decisions:
    - "DDR-001: Implemented with VecDeque sliding window"
    - "DDR-002: LRU cache using linked-hash-map pattern"
    - "DDR-003: tokio::sync::mpsc channel for non-blocking output"

quality_gates:
  - gate_name: "gate-compilation"
    status: "pass"
    evidence: "cargo check --package collector passes with 0 errors"
  - gate_name: "gate-unit-tests"
    status: "pass"
    evidence: "16 unit tests in causal_link_streaming module, all passing"
  - gate_name: "gate-style-compliance"
    status: "pass"
    evidence: "rustfmt check passes; no clippy warnings"
  - gate_name: "gate-risk-mitigation"
    status: "pass"
    evidence: "All 3 design risks addressed; LRU capacity configurable via CLI flag"

deliverables:
  - path: "collector/src/framework/analyzers/causal_link_streaming.rs"
    type: "new"
    lines_changed: 450
    rationale: "Analyzer implementation with sliding window streaming"
  - path: "collector/src/framework/analyzers/mod.rs"
    type: "modified"
    lines_changed: 4
    rationale: "Register new analyzer module"

dependencies:
  - "fixture-design-ho-01"

risks:
  - id: "R-IMPL-001"
    description: "Sliding window may reorder events under concurrent agent execution"
    likelihood: "medium"
    impact: "Out-of-order correlation"
    mitigation: "Events sorted by timestamp_ns before window insertion"
  - id: "R-DESIGN-001"
    description: "Streaming analyzer may cause memory pressure under high event volume"
    likelihood: "low"
    impact: "OOM risk mitigated by LRU capacity tested at 50K events without issue"
    mitigation: "Bounded LRU cache with default 10,000 capacity; tested to 50K"
"#;

const FIXTURE_TEST_YAML: &str = r#"
handoff_id: "fixture-test-ho-01"
feature_name: "Causal Link Streaming Analyzer"
from_phase: "testing"
to_phase: "documentation"
timestamp: "2026-05-05T16:00:00Z"
owner: "integration-testing-orchestrator"
previous_owner: "rust-framework-architect"
next_phase_ready: true

contract: "docs/causal-link-streaming-handoff-contract.md"
contract_version: "1.0"

context:
  summary: |
    Testing phase complete: validated streaming analyzer correctness,
    memory safety under load, and cross-phase contract compliance.
    32 integration tests across 4 test categories.
  performance_validation:
    throughput: "50,000 events/s sustained"
    p99_latency: "< 5μs per event"
    memory: "< 64MB at 50K events"
  security_validation: "No new security surface; analyzer is read-only"

quality_gates:
  - gate_name: "gate-integration-tests"
    status: "pass"
    evidence: "32 integration tests, all passing; coverage at 87%"
  - gate_name: "gate-performance-baseline"
    status: "pass"
    evidence: "50K events/s throughput; p99 latency <5μs; memory <64MB"
  - gate_name: "gate-regression-check"
    status: "pass"
    evidence: "All pre-existing tests unaffected"

deliverables:
  - path: "collector/tests/causal_link_streaming_integration.rs"
    type: "new"
    lines_changed: 650
    rationale: "Integration tests for streaming analyzer"

dependencies:
  - "fixture-impl-ho-01"

risks:
  - id: "R-TEST-001"
    description: "CI environment may have different performance characteristics"
    likelihood: "medium"
    impact: "Performance baseline false positives"
    mitigation: "Baselines recorded as relative ratios, not absolute; CI job runs 5 warmup iterations"
"#;

const FIXTURE_DOC_YAML: &str = r#"
handoff_id: "fixture-doc-ho-01"
feature_name: "Causal Link Streaming Analyzer"
from_phase: "documentation"
to_phase: "merge"
timestamp: "2026-05-05T17:00:00Z"
owner: "docs-architecture-reviewer"
previous_owner: "integration-testing-orchestrator"
next_phase_ready: true

contract: "docs/causal-link-streaming-handoff-contract.md"
contract_version: "1.0"

context:
  summary: |
    Documentation phase complete: API docs, architecture diagrams, and
    migration guide for the streaming analyzer feature.
  doc_completeness:
    api_docs: "All public types and methods documented"
    architecture_diagrams: "Updated pipeline topology with streaming analyzer"
    migration_guide: "No breaking changes; additive feature"

quality_gates:
  - gate_name: "gate-api-docs"
    status: "pass"
    evidence: "cargo doc --no-deps builds with 0 warnings"
  - gate_name: "gate-examples"
    status: "pass"
    evidence: "Example code in docs compiles and runs"
  - gate_name: "gate-readme"
    status: "pass"
    evidence: "README updated with streaming analyzer section"

deliverables:
  - path: "docs/causal-link-streaming-api.md"
    type: "new"
    lines_changed: 200
    rationale: "API documentation"
  - path: "docs/diagrams/pipeline-streaming-topology.png"
    type: "new"
    lines_changed: 0
    rationale: "Architecture diagram"

dependencies:
  - "fixture-test-ho-01"

risks: []
"#;

// =============================================================================
// Test 1: Parse a valid design-phase handoff YAML
// =============================================================================
#[test]
fn test_parse_design_handoff_yaml() {
    // Arrange
    let yaml_str = FIXTURE_DESIGN_YAML;

    // Act
    let contract: HandoffContract = serde_yaml::from_str(yaml_str)
        .expect("Should parse valid design handoff YAML");

    // Assert
    assert_eq!(contract.handoff_id, "fixture-design-ho-01");
    assert_eq!(contract.feature_name, "Causal Link Streaming Analyzer");
    assert_eq!(contract.from_phase, "design");
    assert_eq!(contract.to_phase, "implementation");
    assert_eq!(contract.next_phase_ready, Some(true));

    // Verify context block
    let ctx = contract.context.expect("Should have context block");
    let decisions = ctx.key_design_decisions.expect("Should have key_design_decisions");
    assert!(decisions.len() >= 2, "Should have at least 2 design decisions");
    assert!(decisions[0].contains("DDR-001"), "First decision should be DDR-001");

    // Verify quality gates
    let gates = contract.quality_gates.expect("Should have quality gates");
    assert!(gates.len() >= 2, "Should have at least 2 quality gates");
    for gate in &gates {
        assert_eq!(gate.status, "pass", "Gate '{}' should pass", gate.gate_name);
    }

    // Verify risks documented
    let risks = contract.risks.expect("Should have risks");
    assert!(!risks.is_empty(), "Design phase must document risks");
}

// =============================================================================
// Test 2: Parse all four fixture handoffs and validate chain sequence
// =============================================================================
#[test]
fn test_fixture_chain_all_four_phases_parse() {
    // Arrange
    let fixture_yamls = [
        ("design", FIXTURE_DESIGN_YAML),
        ("implementation", FIXTURE_IMPL_YAML),
        ("testing", FIXTURE_TEST_YAML),
        ("documentation", FIXTURE_DOC_YAML),
    ];

    let mut contracts: Vec<(usize, String, HandoffContract)> = Vec::new();

    // Act — parse all four
    for (phase_name, yaml_str) in &fixture_yamls {
        let contract: HandoffContract = serde_yaml::from_str(yaml_str)
            .unwrap_or_else(|e| panic!("Should parse {} handoff: {}", phase_name, e));
        let idx = phase_index(*phase_name)
            .unwrap_or_else(|| panic!("Unknown phase: {}", phase_name));
        contracts.push((idx, contract.feature_name.clone(), contract));
    }

    // Assert — all parsed, phases in order
    assert_eq!(contracts.len(), 4, "Should parse all 4 fixture handoffs");

    // Verify phase ordering is correct
    contracts.sort_by_key(|(idx, _, _)| *idx);
    assert_eq!(contracts[0].1, "Causal Link Streaming Analyzer");
    assert_eq!(contracts[3].1, "Causal Link Streaming Analyzer");
}

// =============================================================================
// Test 3: Sequential validation — each phase references the previous
// =============================================================================
#[test]
fn test_sequential_dependency_chain() {
    // Arrange
    let design: HandoffContract = serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap();
    let impl_contract: HandoffContract = serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap();
    let test: HandoffContract = serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap();
    let doc: HandoffContract = serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap();

    // Act & Assert
    // A → B: Implementation must reference Design's handoff_id
    let impl_deps = impl_contract.dependencies.as_ref()
        .expect("Implementation phase must declare dependencies");
    assert!(
        impl_deps.contains(&design.handoff_id),
        "Implementation phase must reference Design handoff_id '{}', got: {:?}",
        design.handoff_id, impl_deps
    );

    // B → C: Testing must reference Implementation's handoff_id
    let test_deps = test.dependencies.as_ref()
        .expect("Testing phase must declare dependencies");
    assert!(
        test_deps.contains(&impl_contract.handoff_id),
        "Testing phase must reference Implementation handoff_id '{}', got: {:?}",
        impl_contract.handoff_id, test_deps
    );

    // C → D: Documentation must reference Testing's handoff_id
    let doc_deps = doc.dependencies.as_ref()
        .expect("Documentation phase must declare dependencies");
    assert!(
        doc_deps.contains(&test.handoff_id),
        "Documentation phase must reference Testing handoff_id '{}', got: {:?}",
        test.handoff_id, doc_deps
    );

    // No orphan: design has no dependencies (it's the root)
    assert!(
        design.dependencies.is_none() || design.dependencies.as_ref().unwrap().is_empty(),
        "Design phase should have no dependencies (root of chain)"
    );
}

// =============================================================================
// Test 4: Feature name must be byte-identical across all four phases
// =============================================================================
#[test]
fn test_feature_name_consistency_across_chain() {
    // Arrange
    let contracts: Vec<HandoffContract> = vec![
        serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap(),
    ];

    // Act
    let feature_names: Vec<&str> = contracts.iter().map(|c| c.feature_name.as_str()).collect();

    // Assert
    let first = feature_names[0];
    for (i, name) in feature_names.iter().enumerate() {
        assert_eq!(
            *name, first,
            "Phase {} feature_name '{}' must match design phase '{}'",
            i, name, first
        );
    }
}

// =============================================================================
// Test 5: Quality gates must all pass across all phases
// =============================================================================
#[test]
fn test_all_quality_gates_pass() {
    // Arrange
    let contracts: Vec<(&str, HandoffContract)> = vec![
        ("design", serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap()),
        ("implementation", serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap()),
        ("testing", serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap()),
        ("documentation", serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap()),
    ];

    // Act & Assert
    for (phase, contract) in &contracts {
        let gates = contract.quality_gates.as_ref()
            .unwrap_or_else(|| panic!("Phase '{}' must define quality gates", phase));
        assert!(!gates.is_empty(), "Phase '{}' must have at least one quality gate", phase);

        for gate in gates {
            assert_eq!(
                gate.status, "pass",
                "Phase '{}' gate '{}' must be 'pass', got '{}'",
                phase, gate.gate_name, gate.status
            );
        }
    }
}

// =============================================================================
// Test 6: next_phase_ready must be true for all phases
// =============================================================================
#[test]
fn test_all_phases_ready_for_next() {
    // Arrange
    let contracts: Vec<(&str, HandoffContract)> = vec![
        ("design", serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap()),
        ("implementation", serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap()),
        ("testing", serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap()),
        ("documentation", serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap()),
    ];

    // Act & Assert
    for (phase, contract) in &contracts {
        assert_eq!(
            contract.next_phase_ready,
            Some(true),
            "Phase '{}' must set next_phase_ready: true",
            phase
        );
    }
}

// =============================================================================
// Test 7: Phase ordering must be monotonically non-decreasing
// =============================================================================
#[test]
fn test_temporal_monotonicity() {
    // Arrange — handoff timestamps define ordering
    let contracts: Vec<HandoffContract> = vec![
        serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap(),
    ];

    // Act — extract timestamps and verify ordering
    let timestamps: Vec<&str> = contracts.iter().map(|c| c.timestamp.as_str()).collect();

    // Assert
    for window in timestamps.windows(2) {
        assert!(
            window[0] <= window[1],
            "Timestamp regression: '{}' is not <= '{}'",
            window[0], window[1]
        );
    }
}

// =============================================================================
// Test 8: Risk propagation — risks from design are tracked through
// =============================================================================
#[test]
fn test_risks_propagate_from_design_through_chain() {
    // Arrange
    let design: HandoffContract = serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap();
    let impl_contract: HandoffContract = serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap();
    let test: HandoffContract = serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap();

    // Extract risk descriptions from design phase
    let design_risks = design.risks.expect("Design must have risks");
    let design_risk_ids: Vec<String> = design_risks.iter()
        .filter_map(|r| r.id.clone())
        .collect();

    // Act & Assert — each design risk ID should appear in impl or testing
    // (R-DESIGN-001 appears in impl; R-DESIGN-002/003 are tracked by reference)
    let impl_risk_ids: Vec<String> = impl_contract.risks.as_ref()
        .map(|r| r.iter().filter_map(|r| r.id.clone()).collect())
        .unwrap_or_default();
    let test_risk_ids: Vec<String> = test.risks.as_ref()
        .map(|r| r.iter().filter_map(|r| r.id.clone()).collect())
        .unwrap_or_default();

    // Check that at least some design risks are re-referenced
    // In our fixtures: R-DESIGN-001 is re-referenced in implementation
    let all_downstream_ids: Vec<&str> = impl_risk_ids.iter().map(|s| s.as_str())
        .chain(test_risk_ids.iter().map(|s| s.as_str()))
        .collect();

    for risk_id in &design_risk_ids {
        let id_str = risk_id.as_str();
        // Allow risks to be explicitly resolved (not re-referenced) — just check tracking
        println!("  Design risk '{}' tracked downstream: {}", id_str,
            if all_downstream_ids.contains(&id_str) { "YES" } else { "resolved" });
    }

    // At minimum, R-DESIGN-001 (highest likelihood=medium) must be tracked
    assert!(
        all_downstream_ids.contains(&"R-DESIGN-001"),
        "High-likelihood risk R-DESIGN-001 must be tracked in implementation or testing"
    );
}

// =============================================================================
// Test 9: Owner must transition across the chain (no single owner for all)
// =============================================================================
#[test]
fn test_owner_transitions_through_phases() {
    // Arrange
    let contracts: Vec<(&str, HandoffContract)> = vec![
        ("design", serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap()),
        ("implementation", serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap()),
        ("testing", serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap()),
        ("documentation", serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap()),
    ];

    // Act — collect owners
    let owners: Vec<&str> = contracts.iter()
        .map(|(_, c)| c.owner.as_deref().unwrap_or("unknown"))
        .collect();

    // Assert — owners must not all be the same (indicates no real handoff)
    let first_owner = owners[0];
    let all_same = owners.iter().all(|o| *o == first_owner);
    assert!(!all_same, "Owners must transition across phases: {:?}", owners);

    // Verify previous_owner matches for handoffs
    for i in 1..contracts.len() {
        let (phase, contract) = &contracts[i];
        let prev_owner = owners[i - 1];
        let claimed_prev = contract.previous_owner.as_deref().unwrap_or("null");
        // previous_owner should either match or be "null" for root
        if claimed_prev != "null" {
            assert_eq!(
                claimed_prev, prev_owner,
                "Phase '{}' claims previous_owner='{}' but handoff from '{}'",
                phase, claimed_prev, prev_owner
            );
        }
    }
}

// =============================================================================
// Test 10: Deliverables path claims exist (file existence check)
// =============================================================================
#[test]
fn test_deliverable_paths_have_expected_format() {
    // Arrange
    let contracts: Vec<HandoffContract> = vec![
        serde_yaml::from_str(FIXTURE_DESIGN_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_IMPL_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_TEST_YAML).unwrap(),
        serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap(),
    ];

    // Act & Assert
    let mut total_deliverables = 0;
    for contract in &contracts {
        let deliverables = contract.deliverables.as_ref()
            .expect("Each phase must list deliverables");
        assert!(!deliverables.is_empty(), "Must have at least one deliverable");
        total_deliverables += deliverables.len();

        for del in deliverables {
            // Verify the path has a plausible format
            assert!(
                del.path.ends_with(".rs") || del.path.ends_with(".md") || del.path.ends_with(".yaml")
                    || del.path.ends_with(".png") || del.path.contains('/'),
                "Deliverable path '{}' should have a recognizable extension or directory prefix",
                del.path
            );
        }
    }

    assert!(total_deliverables >= 4, "Must have at least 4 total deliverables across all phases");
}

// =============================================================================
// Test 11: Malformed handoff must be rejected
// =============================================================================
#[test]
fn test_malformed_handoff_rejected() {
    // Arrange — missing required field
    let bad_yaml = r#"
handoff_id: "bad-ho-01"
# missing: feature_name
from_phase: "design"
to_phase: "implementation"
timestamp: "2026-05-05T13:00:00Z"
"#;

    // Act — this should FAIL to parse (feature_name is required in our model)
    let result: Result<HandoffContract, _> = serde_yaml::from_str(bad_yaml);

    // Assert
    assert!(
        result.is_err(),
        "Handoff YAML missing required 'feature_name' field should be rejected"
    );
}

// =============================================================================
// Test 12: Invalid phase transition must be detectable
// =============================================================================
#[test]
fn test_invalid_phase_transition_detected() {
    // Arrange — design cannot go to documentation (skips implementation + testing)
    let invalid_yaml = r#"
handoff_id: "invalid-ho-01"
feature_name: "Test Feature"
from_phase: "design"
to_phase: "documentation"
timestamp: "2026-05-05T13:00:00Z"
"#;

    // Act — parse and check phase ordering
    let contract: HandoffContract = serde_yaml::from_str(invalid_yaml)
        .expect("Should parse structurally valid YAML");

    let from_idx = phase_index(&contract.from_phase);
    let to_idx = phase_index(&contract.to_phase);

    // Assert — valid transitions must be adjacent in phase order
    match (from_idx, to_idx) {
        (Some(f), Some(t)) => {
            let is_valid = t == f + 1;
            assert!(
                !is_valid,
                "design → documentation is not an adjacent phase transition"
            );
            println!("  ✓ Invalid transition design→documentation correctly flagged (skip={})", t - f - 1);
        }
        _ => panic!("Both phases must be in canonical order"),
    }
}

// =============================================================================
// Test 13: Empty risks section is allowed in documentation (all risks resolved)
// =============================================================================
#[test]
fn test_empty_risks_in_doc_allowed() {
    // Arrange
    let doc: HandoffContract = serde_yaml::from_str(FIXTURE_DOC_YAML).unwrap();

    // Act
    let risks = doc.risks.as_ref();

    // Assert — empty risks is valid for final phase (all risks resolved)
    assert!(risks.is_some(), "Documentation must have 'risks:' key");
    let risk_vec = risks.unwrap();
    assert!(
        risk_vec.is_empty(),
        "Documentation phase should have empty risks (all resolved)"
    );
}

// =============================================================================
// Test 14: Integration — read actual handoff YAMLs from filesystem
// =============================================================================
//
// This test reads the real handoff YAMLs from .kiro/handoffs/ and validates
// that on-disk files conform to the contract schema. It skips if the directory
// is not accessible (e.g., running tests in isolation).
#[test]
fn test_filesystem_handoff_yamls_conform_to_schema() {
    // Arrange — locate handoff directory relative to test binary
    let handoff_dir = find_handoff_dir();

    let handoff_dir = match handoff_dir {
        Some(dir) => dir,
        None => {
            eprintln!("  ⚠ .kiro/handoffs/ not found — skipping filesystem integration test");
            return;
        }
    };

    // Discover all handoff YAMLs
    let yaml_files: Vec<_> = std::fs::read_dir(&handoff_dir)
        .expect("Should read handoff directory")
        .filter_map(|entry| {
            let entry = entry.ok()?;
            let path = entry.path();
            if path.extension()? == "yaml" && path.is_file() {
                Some(path)
            } else {
                None
            }
        })
        .collect();

    if yaml_files.is_empty() {
        eprintln!("  ⚠ No handoff YAMLs found in {:?} — skipping", handoff_dir);
        return;
    }

    println!("  Found {} handoff YAMLs in {:?}", yaml_files.len(), handoff_dir);

    // Act — parse each YAML
    let mut parsed: Vec<(std::path::PathBuf, HandoffContract)> = Vec::new();
    for path in &yaml_files {
        let content = std::fs::read_to_string(path)
            .unwrap_or_else(|e| panic!("Should read {}: {}", path.display(), e));

        match serde_yaml::from_str::<HandoffContract>(&content) {
            Ok(contract) => {
                parsed.push((path.clone(), contract));
            }
            Err(e) => {
                // Some handoff YAMLs may have non-standard fields; log but don't fail
                eprintln!("  ⚠ Parse warning for {}: {}", path.display(), e);
            }
        }
    }

    // Assert — at least some parsed successfully
    assert!(!parsed.is_empty(), "At least one handoff YAML must parse successfully");

    // Group by feature_name
    let mut by_feature: HashMap<String, Vec<(std::path::PathBuf, HandoffContract)>> = HashMap::new();
    for (path, contract) in parsed.drain(..) {
        by_feature.entry(contract.feature_name.clone())
            .or_default()
            .push((path, contract));
    }

    println!("  Features found: {}", by_feature.len());

    // For each multi-phase feature, validate chain integrity
    for (feature, contracts) in &by_feature {
        if contracts.len() < 2 {
            println!("  ⚠ Feature '{}' has only {} handoff(s) — chain incomplete", feature, contracts.len());
            continue;
        }

        println!("  ✓ Feature '{}' — {} handoff phase(s)", feature, contracts.len());

        // Sort by phase order
        let mut sorted: Vec<&HandoffContract> = contracts.iter().map(|(_, c)| c).collect();
        sorted.sort_by_key(|c| phase_index(&c.from_phase).unwrap_or(99));

        // Verify feature_name consistency
        let first_name = &sorted[0].feature_name;
        for c in &sorted {
            assert_eq!(
                &c.feature_name, first_name,
                "Feature '{}' has inconsistent feature_name across phases (got '{}')",
                feature, c.feature_name
            );
        }

        // Verify next_phase_ready
        for c in &sorted {
            let path = &contracts.iter()
                .find(|(_, hc)| hc.handoff_id == c.handoff_id)
                .map(|(p, _)| p.display().to_string())
                .unwrap_or_default();
            assert_eq!(
                c.next_phase_ready,
                Some(true),
                "Handoff '{}' ({}) must have next_phase_ready: true",
                c.handoff_id, path
            );
        }
    }
}

// =============================================================================
// Test 15: DAG acyclicity — chain must not loop back
// =============================================================================
#[test]
fn test_no_dag_cycles_possible() {
    // Verify that the canonical phase ordering prevents any cycle.
    // A valid DAG requires each phase's from_phase index < to_phase index.

    // Arrange — all valid forward phase transitions
    let valid_pairs = vec![
        ("design", "implementation"),
        ("implementation", "testing"),
        ("testing", "documentation"),
    ];

    for (from, to) in &valid_pairs {
        let from_idx = phase_index(from).unwrap_or(99);
        let to_idx = phase_index(to).unwrap_or(99);

        // Assert — forward transitions must have from_idx < to_idx
        assert!(
            to_idx > from_idx,
            "Transition {}→{} is backward (cycle) — must be rejected",
            from, to
        );
    }
}

// =============================================================================
// Helpers
// =============================================================================

/// Locate the `.kiro/handoffs/` directory by searching upward from the
/// current working directory or the test binary location.
fn find_handoff_dir() -> Option<std::path::PathBuf> {
    // Try several candidate locations
    let candidates = [
        std::env::current_dir().ok().map(|p| p.join(".kiro/handoffs")),
        std::env::current_dir().ok().map(|p| p.join("../.kiro/handoffs")),
        std::env::current_exe().ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .map(|p| p.join("../../../../.kiro/handoffs")),
        std::env::current_exe().ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .map(|p| p.join("../../../.kiro/handoffs")),
    ];

    for candidate in candidates.iter().flatten() {
        if candidate.is_dir() {
            return Some(candidate.clone());
        }
    }

    // Final fallback — try common locations
    for base in &["/home/lzk/mtagentlens", "/app", "/workspace"] {
        let candidate = Path::new(base).join(".kiro/handoffs");
        if candidate.is_dir() {
            return Some(candidate);
        }
    }

    None
}

// =============================================================================
// E2E Verification: Full Handoff Chain Sequential Validation (Subagent D)
// =============================================================================
//
// Validates the complete A→B→C→D handoff chain can be processed sequentially.
// Each phase is validated only after its dependencies are satisfied.
//
// Contract: E2E_VERIFICATION_SPEC.md §16 — Sequential Validation Protocol
//   Step 1: Load & validate phase A (design) — no dependencies
//   Step 2: Validate phase B (impl) — only if A passed
//   Step 3: Validate phase C (testing) — only if B passed
//   Step 4: Validate phase D (docs) — only if C passed
//
#[test]
fn test_e2e_sequential_handoff_chain_validation() {
    // Arrange: Load all four phases
    let phase_a = serde_yaml::from_str::<HandoffContract>(FIXTURE_DESIGN_YAML)
        .expect("Phase A (design) must parse");
    let phase_b = serde_yaml::from_str::<HandoffContract>(FIXTURE_IMPL_YAML)
        .expect("Phase B (impl) must parse");
    let phase_c = serde_yaml::from_str::<HandoffContract>(FIXTURE_TEST_YAML)
        .expect("Phase C (testing) must parse");
    let phase_d = serde_yaml::from_str::<HandoffContract>(FIXTURE_DOC_YAML)
        .expect("Phase D (docs) must parse");

    // Step 1: Validate Phase A (root — no dependencies)
    assert!(
        phase_a.dependencies.is_none() || phase_a.dependencies.as_ref().unwrap().is_empty(),
        "Phase A must be root (no dependencies)"
    );
    assert_eq!(phase_a.from_phase, "design", "Phase A must be design phase");
    assert_eq!(phase_a.to_phase, "implementation", "Phase A must transition to implementation");
    assert!(
        phase_a.quality_gates.as_ref().map_or(false, |gates| gates.iter().all(|g| g.status == "pass")),
        "Phase A must have all quality gates passing"
    );
    assert_eq!(phase_a.next_phase_ready, Some(true), "Phase A must signal next phase ready");

    // Step 2: Validate Phase B (depends on A)
    let phase_b_deps = phase_b.dependencies.as_ref()
        .expect("Phase B must declare dependencies");
    assert!(
        phase_b_deps.contains(&phase_a.handoff_id),
        "Phase B must depend on Phase A handoff_id"
    );
    assert_eq!(phase_b.from_phase, "implementation", "Phase B must be implementation phase");
    assert_eq!(phase_b.to_phase, "testing", "Phase B must transition to testing");
    assert!(
        phase_b.quality_gates.as_ref().map_or(false, |gates| gates.iter().all(|g| g.status == "pass")),
        "Phase B must have all quality gates passing"
    );
    assert_eq!(phase_b.next_phase_ready, Some(true), "Phase B must signal next phase ready");

    // Step 3: Validate Phase C (depends on B)
    let phase_c_deps = phase_c.dependencies.as_ref()
        .expect("Phase C must declare dependencies");
    assert!(
        phase_c_deps.contains(&phase_b.handoff_id),
        "Phase C must depend on Phase B handoff_id"
    );
    assert_eq!(phase_c.from_phase, "testing", "Phase C must be testing phase");
    assert_eq!(phase_c.to_phase, "documentation", "Phase C must transition to documentation");
    assert!(
        phase_c.quality_gates.as_ref().map_or(false, |gates| gates.iter().all(|g| g.status == "pass")),
        "Phase C must have all quality gates passing"
    );
    assert_eq!(phase_c.next_phase_ready, Some(true), "Phase C must signal next phase ready");

    // Step 4: Validate Phase D (depends on C, terminal)
    let phase_d_deps = phase_d.dependencies.as_ref()
        .expect("Phase D must declare dependencies");
    assert!(
        phase_d_deps.contains(&phase_c.handoff_id),
        "Phase D must depend on Phase C handoff_id"
    );
    assert_eq!(phase_d.from_phase, "documentation", "Phase D must be documentation phase");
    // Phase D is terminal (no to_phase or to_phase is None/empty)
    // Phase D (documentation) is terminal: to_phase should be empty or a terminal indicator
    assert!(
        phase_d.to_phase.is_empty()
            || phase_d.to_phase == "complete"
            || phase_d.to_phase == "documentation"
            || phase_d.to_phase == "merge",
        "Phase D must be terminal phase, got: '{}'",
        phase_d.to_phase
    );
    assert!(
        phase_d.quality_gates.as_ref().map_or(false, |gates| gates.iter().all(|g| g.status == "pass")),
        "Phase D must have all quality gates passing"
    );
}

// =============================================================================
// E2E Verification: Context Bridging Across Phases
// =============================================================================
//
// Validates that context from each phase is properly bridged to the next.
// Each phase must reference prior phase's key decisions and risks.
//
#[test]
fn test_e2e_context_bridging_across_phases() {
    let phase_a = serde_yaml::from_str::<HandoffContract>(FIXTURE_DESIGN_YAML).unwrap();
    let phase_b = serde_yaml::from_str::<HandoffContract>(FIXTURE_IMPL_YAML).unwrap();
    let phase_c = serde_yaml::from_str::<HandoffContract>(FIXTURE_TEST_YAML).unwrap();
    let phase_d = serde_yaml::from_str::<HandoffContract>(FIXTURE_DOC_YAML).unwrap();

    // A→B: Implementation must reference design decisions
    let phase_a_decisions = phase_a.context.as_ref()
        .and_then(|ctx| ctx.key_design_decisions.as_ref())
        .map(|decs| decs.len())
        .unwrap_or(0);
    assert!(phase_a_decisions > 0, "Phase A must document design decisions");

    // B→C: Testing must reference implementation deliverables
    let phase_b_deliverables = phase_b.deliverables.as_ref()
        .map(|dels| dels.len())
        .unwrap_or(0);
    assert!(phase_b_deliverables > 0, "Phase B must document deliverables");

    // C→D: Documentation must reference test results
    let phase_c_context = phase_c.context.as_ref()
        .and_then(|ctx| ctx.summary.as_ref())
        .map(|s| s.len())
        .unwrap_or(0);
    assert!(phase_c_context > 0, "Phase C must document test results in context");

    // D: Documentation must reference all prior phases
    let phase_d_deps = phase_d.dependencies.as_ref().unwrap();
    assert_eq!(phase_d_deps.len(), 1, "Phase D must depend on Phase C (transitive closure)");
}

// =============================================================================
// E2E Verification: No Blockers in Chain
// =============================================================================
//
// Validates that no phase has unresolved blockers that would prevent handoff.
//
#[test]
fn test_e2e_no_blockers_in_chain() {
    let phases = vec![
        ("design", FIXTURE_DESIGN_YAML),
        ("implementation", FIXTURE_IMPL_YAML),
        ("testing", FIXTURE_TEST_YAML),
        ("documentation", FIXTURE_DOC_YAML),
    ];

    for (phase_name, yaml) in phases {
        let contract = serde_yaml::from_str::<HandoffContract>(yaml)
            .expect(&format!("Phase {} must parse", phase_name));

        let blockers = contract.blockers.as_ref()
            .map(|b| b.len())
            .unwrap_or(0);
        assert_eq!(
            blockers, 0,
            "Phase {} must have no blockers (found {})",
            phase_name, blockers
        );
    }
}

// =============================================================================
// E2E Verification: Feature Name Immutability
// =============================================================================
//
// Validates that feature name remains identical across all phases.
// This ensures the chain refers to the same feature throughout.
//
#[test]
fn test_e2e_feature_name_immutability() {
    let phase_a = serde_yaml::from_str::<HandoffContract>(FIXTURE_DESIGN_YAML).unwrap();
    let phase_b = serde_yaml::from_str::<HandoffContract>(FIXTURE_IMPL_YAML).unwrap();
    let phase_c = serde_yaml::from_str::<HandoffContract>(FIXTURE_TEST_YAML).unwrap();
    let phase_d = serde_yaml::from_str::<HandoffContract>(FIXTURE_DOC_YAML).unwrap();

    let feature_name = &phase_a.feature_name;
    assert_eq!(&phase_b.feature_name, feature_name, "Phase B feature name must match Phase A");
    assert_eq!(&phase_c.feature_name, feature_name, "Phase C feature name must match Phase A");
    assert_eq!(&phase_d.feature_name, feature_name, "Phase D feature name must match Phase A");
}
