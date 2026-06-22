use std::fs;
use std::path::PathBuf;

/// Read file content, returning None if the file doesn't exist (graceful skip).
fn read_file_if_exists(path: &std::path::Path) -> Option<String> {
    fs::read_to_string(path).ok()
}

#[test]
fn new_responsibility_contract_files_are_old_term_free() {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let strict_paths = [
        "src/accountability/observation/mod.rs",
        "src/accountability/observation/event_model.rs",
        "src/accountability/observation/normalizer.rs",
        "src/accountability/identity/binding.rs",
        "src/accountability/identity/resolver.rs",
        "src/accountability/evidence/evidence_record.rs",
        "src/accountability/evidence/reconstructor.rs",
        "src/accountability/graph/builder.rs",
        "src/accountability/graph/responsibility_node.rs",
        "src/accountability/graph/responsibility_edge.rs",
        "src/accountability/judgment/judgment_record.rs",
        "src/accountability/judgment/policy_rule.rs",
        "src/accountability/observer/mod.rs",
        "src/accountability/observer/provider.rs",
        "src/accountability/observer/mock.rs",
        "src/accountability/observer/system_prompt.rs",
        "src/accountability/claims/accountability_claim.rs",
        "src/accountability/claims/alert_writer.rs",
        "src/accountability/claims/alert.rs",
        "src/accountability/claims/alert_builder.rs",
        "src/accountability/claims/alert_builder_title.rs",
        "src/accountability/claims/claim_builder.rs",
        "src/accountability/claims/persistence.rs",
        "src/accountability/claims/projection.rs",
        "src/accountability/claims/traceback.rs",
        "src/accountability/graph/pattern_matcher.rs",
        "src/accountability/persistence/responsibility_schema.rs",
        "tests/responsibility_alert_builder.rs",
        "tests/responsibility_claim_builder.rs",
        "tests/responsibility_projection.rs",
        "tests/responsibility_traceback.rs",
        "tests/responsibility_pattern_matcher.rs",
        "tests/responsibility_persistence.rs",
        "tests/responsibility_core_contracts.rs",
        "tests/responsibility_evidence_reconstruction.rs",
        "tests/responsibility_graph_builder.rs",
        "tests/responsibility_observation_identity.rs",
        "src/accountability/classification/types.rs",
        "src/accountability/classification/rules.rs",
        "src/accountability/classification/rule_engine.rs",
        "src/accountability/classification/pattern_matcher.rs",
        "src/accountability/classification/scoring.rs",
        "src/accountability/classification/observer_llm.rs",
        "src/accountability/classification/observer_cache.rs",
        "src/accountability/classification/mod.rs",
        "src/accountability/runtime/buffer_new.rs",
        "src/accountability/runtime/runtime_new.rs",
        "src/accountability/runtime/mod.rs",
    ];
    let forbidden = [
        "causal_link",
        "causal_links",
        "causal_trace",
        "/causal-links",
        "causalLinkIds",
    ];

    let mut violations = Vec::new();
    for relative in strict_paths {
        let path = manifest_dir.join(relative);
        let Some(content) = read_file_if_exists(&path) else {
            // File was removed during refactoring — skip silently
            continue;
        };
        for term in forbidden {
            if content.contains(term) {
                violations.push(format!("{} contains {}", relative, term));
            }
        }
    }

    assert!(violations.is_empty(), "{}", violations.join("\n"));
}

#[test]
fn phase_a_export_points_do_not_introduce_new_old_terms() {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let export_paths = [
        "src/accountability/observation/mod.rs",
        "src/accountability/observer/mod.rs",
        "src/accountability/identity/mod.rs",
        "src/accountability/evidence/mod.rs",
        "src/accountability/graph/mod.rs",
        "src/accountability/judgment/mod.rs",
        "src/accountability/claims/mod.rs",
        "src/accountability/classification/mod.rs",
        "src/accountability/persistence/mod.rs",
        "src/accountability/runtime/mod.rs",
    ];
    let forbidden = [
        "causal_link",
        "causal_links",
        "causal_trace",
        "/causal-links",
        "causalLinkIds",
    ];

    let mut violations = Vec::new();
    for relative in export_paths {
        let path = manifest_dir.join(relative);
        let Some(content) = read_file_if_exists(&path) else {
            // File was removed during refactoring — skip silently
            continue;
        };
        for term in forbidden {
            if content.contains(term) {
                violations.push(format!("{} contains {}", relative, term));
            }
        }
    }

    assert!(violations.is_empty(), "{}", violations.join("\n"));
}
