// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use serde::Deserialize;
use serde_json::{Value, json};
use std::path::Path;
use std::sync::OnceLock;

#[derive(Debug, Deserialize)]
pub struct AccountabilityEvidenceContract {
    pub kind: String,
    pub directness_values: Vec<String>,
    pub allow_missing_or_null_boundary_id: bool,
    pub require_non_empty_string_evidence_refs: bool,
    pub require_correlation_evidence_object: bool,
}

static ACCOUNTABILITY_EVIDENCE_CONTRACT: OnceLock<AccountabilityEvidenceContract> = OnceLock::new();

pub fn accountability_evidence_contract() -> &'static AccountabilityEvidenceContract {
    ACCOUNTABILITY_EVIDENCE_CONTRACT.get_or_init(|| {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("env")
            .join("config")
            .join("accountability_evidence_contract.json");
        let payload = std::fs::read_to_string(&path).unwrap_or_else(|err| {
            panic!(
                "failed to read accountability evidence contract {}: {err}",
                path.display()
            )
        });
        serde_json::from_str(&payload).unwrap_or_else(|err| {
            panic!(
                "failed to parse accountability evidence contract {}: {err}",
                path.display()
            )
        })
    })
}

pub fn accountability_evidence_shape_is_valid(evidence: &Value) -> bool {
    let contract = accountability_evidence_contract();
    let Some(obj) = evidence.as_object() else {
        return false;
    };
    if obj.get("kind").and_then(Value::as_str) != Some(contract.kind.as_str()) {
        return false;
    }
    if contract.require_non_empty_string_evidence_refs
        && !matches!(
            obj.get("evidence_refs"),
            Some(Value::Array(refs)) if !refs.is_empty() && refs.iter().all(Value::is_string)
        )
    {
        return false;
    }
    if !obj
        .get("directness")
        .and_then(Value::as_str)
        .is_some_and(|value| {
            contract
                .directness_values
                .iter()
                .any(|allowed| allowed == value)
        })
    {
        return false;
    }
    if contract.allow_missing_or_null_boundary_id
        && !matches!(
            obj.get("boundary_id"),
            Some(Value::Null) | None | Some(Value::String(_))
        )
    {
        return false;
    }
    if contract.require_correlation_evidence_object {
        return matches!(obj.get("correlation_evidence"), Some(Value::Object(_)));
    }
    true
}

pub fn accountability_boundary_id(candidate_type: Option<&str>) -> Option<&'static str> {
    match candidate_type {
        Some("file_read") => Some("boundary.file_read"),
        Some("file_write") | Some("file_overwrite") => Some("boundary.file_write"),
        Some("network_connect") => Some("boundary.network_connect"),
        Some("process_exec") => Some("boundary.process_exec"),
        _ => None,
    }
}

pub fn build_accountability_evidence_payload(data: &Value, link_id: &str) -> Value {
    let contract = accountability_evidence_contract();
    let evidence_id = format!("evidence:{}", link_id);
    let directness = if data
        .get("candidate")
        .and_then(|candidate| candidate.get("derivation_mode"))
        .and_then(|v| v.as_str())
        == Some("direct")
    {
        contract.directness_values[0].as_str()
    } else {
        contract.directness_values[1].as_str()
    };
    let boundary_id = accountability_boundary_id(
        data.get("candidate")
            .and_then(|candidate| candidate.get("candidate_type"))
            .and_then(|v| v.as_str()),
    );

    json!({
        "kind": contract.kind,
        "evidence_refs": [evidence_id],
        "directness": directness,
        "boundary_id": boundary_id,
        "correlation_evidence": data,
    })
}
