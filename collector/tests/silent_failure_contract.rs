// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Guards for the silent-failure fixes recorded in ADR-0013.
//!
//! Every assertion here protects an observable behaviour that previously
//! failed silently. They are source-level because the failure modes are about
//! *wiring* (a counter not emitted, an error swallowed), which unit tests of
//! the individual pieces cannot prove.

use std::fs;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("crate is inside the repository")
        .to_path_buf()
}

fn read(rel: &str) -> String {
    let path = repo_root().join(rel);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()))
}

#[test]
fn routing_counters_are_emitted_at_finalize() {
    let runtime = read("collector/src/responsibility/runtime/runtime.rs");
    assert!(
        runtime.contains("[diagnostics:json]"),
        "runtime must emit a diagnostics line; an empty run has to be distinguishable from an unobserved one"
    );
    for counter in [
        "DIAG_EVIDENCE_ATTEMPTED",
        "DIAG_EVIDENCE_INTENTS",
        "DIAG_ACTIONS_PRODUCED",
        "DIAG_ACTIONS_DROPPED_NO_AGENT",
        "DIAG_ACTIONS_DROPPED_BPF_NOISE",
        "DIAG_INTENTS_DEDUPED",
        "DIAG_BODY_TOO_LARGE",
        "DIAG_OPENCLAW_TOOL_CALLS",
    ] {
        assert!(
            runtime.contains(counter),
            "{counter} must be included in the diagnostics output"
        );
    }
}

#[test]
fn agent_comm_boundary_is_configurable() {
    let main = read("collector/src/main.rs");
    assert!(
        main.contains("configure_agent_comms_from_env"),
        "main must apply MTAGENTLENS_AGENT_COMMS before routing any event"
    );
    let union_find = read("collector/src/responsibility/identity/union_find.rs");
    assert!(
        union_find.contains("pub fn is_agent_comm"),
        "a single is_agent_comm() must replace direct AGENT_COMMS.contains() calls"
    );
    assert!(
        union_find.contains("MTAGENTLENS_AGENT_COMMS"),
        "the override must be documented and read from the environment"
    );
}

#[test]
fn reader_never_swallows_a_corrupt_json_column() {
    let reader = read("collector/src/responsibility/persistence/reader.rs");
    for line in reader.lines() {
        if !line.contains("deser_json(") {
            continue;
        }
        assert!(
            !line.contains(".unwrap_or_default()") && !line.contains(".unwrap_or_else("),
            "deser_json must propagate, not fabricate an empty structure: {line}"
        );
    }
    // The only tolerated shape is the schema's own "not recorded" default.
    assert!(
        reader.contains("val.trim().is_empty()"),
        "empty columns mean 'not recorded' and must be handled explicitly"
    );
}

#[test]
fn writer_counts_closed_channel_rejections() {
    let writer = read("collector/src/responsibility/persistence/writer.rs");
    assert!(
        writer.contains("TrySendError::Closed"),
        "the closed arm must exist"
    );
    assert!(
        writer.contains("closed_count"),
        "the closed arm must increment a counter"
    );
    let runtime = read("collector/src/responsibility/runtime/runtime.rs");
    assert!(
        runtime.contains("channel_closed_count"),
        "finalize must include the closed-channel count in its DEGRADED decision"
    );
}

#[test]
fn observer_config_errors_are_not_reported_as_missing() {
    let provider = read("collector/src/responsibility/observer/provider.rs");
    assert!(
        provider.contains("pub fn load_from_paths"),
        "config loading must distinguish 'not configured' from 'configured but invalid'"
    );
    let main = read("collector/src/main.rs");
    assert!(
        main.contains("observer config exists but is unusable"),
        "an unparseable observer config must stop the process"
    );
}

#[test]
fn evidence_is_only_written_when_its_parents_reached_the_writer() {
    let runtime = read("collector/src/responsibility/runtime/runtime.rs");
    assert!(
        runtime.contains("persisted_intent_ids") && runtime.contains("persisted_action_ids"),
        "evidence persistence must be guarded by the set of parents that were actually queued"
    );
    let writer = read("collector/src/responsibility/persistence/writer.rs");
    assert!(
        writer.contains("pub async fn write_blocking"),
        "batch/finalize paths need a blocking send so parents are not dropped"
    );
}

#[test]
fn inert_configuration_types_are_gone() {
    let runners = read("collector/src/framework/runners/mod.rs");
    assert!(
        !runners.contains("pub struct SslConfig") && !runners.contains("pub struct ProcessConfig"),
        "no-op runner configs were deleted; reintroducing one needs a real reader"
    );
    let executor = read("collector/src/responsibility/replay/executor.rs");
    assert!(
        !executor.contains("pub struct ReplayConfig"),
        "ablation switches that the executor never reads must not come back as no-ops"
    );
}
