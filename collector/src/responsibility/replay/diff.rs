// SPDX-License-Identifier: MIT
//
// diff.rs — Cross-run causal graph diff analysis.
//
// Compares two sets of CorrelationEvidence (from different runs) and produces
// a RunDiff showing which operation types appeared, disappeared, or changed
// in frequency.
//
// Also provides OpDiff for per-operation-type breakdown.

use crate::responsibility::types::CorrelationEvidence;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ═══════════════════════════════════════════════════════════════════════════
// DiffChange
// ═══════════════════════════════════════════════════════════════════════════

/// The nature of a change between two runs for a given operation type.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum DiffChange {
    Added,
    Removed,
    Increased,
    Decreased,
    Unchanged,
}

// ═══════════════════════════════════════════════════════════════════════════
// OpDiff
// ═══════════════════════════════════════════════════════════════════════════

/// Per-operation-type diff entry comparing two runs.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpDiff {
    pub op_type: String,
    pub count_before: usize,
    pub count_after: usize,
    pub delta: i64,
    pub change: DiffChange,
}

// ═══════════════════════════════════════════════════════════════════════════
// RunDiff
// ═══════════════════════════════════════════════════════════════════════════

/// Full cross-run diff result.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunDiff {
    pub op_diffs: Vec<OpDiff>,
    pub total_before: usize,
    pub total_after: usize,
    pub summary: String,
}

// ═══════════════════════════════════════════════════════════════════════════
// Typed diff function
// ═══════════════════════════════════════════════════════════════════════════

/// Compare two sets of correlation evidence from different runs.
///
/// Groups evidence by operation type (derived from the matched fields or
/// the associated action type) and produces an `OpDiff` for each type
/// that appears in either run.
///
/// The `action_types` map provides the action_type string for each evidence's
/// action_id — this is used to produce meaningful op_type labels instead of
/// "unknown".
pub fn diff_runs_typed(
    before: &[CorrelationEvidence],
    after: &[CorrelationEvidence],
    action_types: &HashMap<String, String>,
) -> RunDiff {
    let count_ops = |evidence: &[CorrelationEvidence]| -> HashMap<String, usize> {
        let mut m: HashMap<String, usize> = HashMap::new();
        for ev in evidence {
            let op = action_types
                .get(ev.action_id.as_str())
                .cloned()
                .unwrap_or_else(|| format!("{:?}", ev.match_level));
            *m.entry(op).or_default() += 1;
        }
        m
    };

    let before_counts = count_ops(before);
    let after_counts = count_ops(after);

    let all_ops: std::collections::HashSet<&str> = before_counts
        .keys()
        .chain(after_counts.keys())
        .map(|s| s.as_str())
        .collect();

    let mut op_diffs: Vec<OpDiff> = all_ops
        .iter()
        .map(|op| {
            let b = before_counts.get(*op).copied().unwrap_or(0);
            let a = after_counts.get(*op).copied().unwrap_or(0);
            let delta = a as i64 - b as i64;
            let change = match (b, a) {
                (0, _) => DiffChange::Added,
                (_, 0) => DiffChange::Removed,
                _ if delta > 0 => DiffChange::Increased,
                _ if delta < 0 => DiffChange::Decreased,
                _ => DiffChange::Unchanged,
            };
            OpDiff {
                op_type: op.to_string(),
                count_before: b,
                count_after: a,
                delta,
                change,
            }
        })
        .collect();

    op_diffs.sort_by(|a, b| b.delta.abs().cmp(&a.delta.abs()));

    let added: Vec<&str> = op_diffs
        .iter()
        .filter(|d| d.change == DiffChange::Added)
        .map(|d| d.op_type.as_str())
        .collect();
    let removed: Vec<&str> = op_diffs
        .iter()
        .filter(|d| d.change == DiffChange::Removed)
        .map(|d| d.op_type.as_str())
        .collect();

    let summary = if added.is_empty() && removed.is_empty() {
        format!(
            "No structural changes between runs ({} → {} links).",
            before.len(),
            after.len()
        )
    } else {
        let mut parts = vec![];
        if !added.is_empty() {
            parts.push(format!("New operations: {}", added.join(", ")));
        }
        if !removed.is_empty() {
            parts.push(format!("Removed operations: {}", removed.join(", ")));
        }
        parts.join("; ")
    };

    RunDiff {
        op_diffs,
        total_before: before.len(),
        total_after: after.len(),
        summary,
    }
}

/// Compare two sets of correlation evidence using JSON Value data (backward
/// compatible with the existing `diff_runs` API).
pub fn diff_runs(before: &[serde_json::Value], after: &[serde_json::Value]) -> RunDiff {
    let count_ops = |links: &[serde_json::Value]| -> HashMap<String, usize> {
        let mut m: HashMap<String, usize> = HashMap::new();
        for link in links {
            let op = link
                .get("matched_fields")
                .and_then(|f| f.get("candidate_type"))
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            *m.entry(op).or_default() += 1;
        }
        m
    };

    let before_counts = count_ops(before);
    let after_counts = count_ops(after);

    let all_ops: std::collections::HashSet<&str> = before_counts
        .keys()
        .chain(after_counts.keys())
        .map(|s| s.as_str())
        .collect();

    let mut op_diffs: Vec<OpDiff> = all_ops
        .iter()
        .map(|op| {
            let b = before_counts.get(*op).copied().unwrap_or(0);
            let a = after_counts.get(*op).copied().unwrap_or(0);
            let delta = a as i64 - b as i64;
            let change = match (b, a) {
                (0, _) => DiffChange::Added,
                (_, 0) => DiffChange::Removed,
                _ if delta > 0 => DiffChange::Increased,
                _ if delta < 0 => DiffChange::Decreased,
                _ => DiffChange::Unchanged,
            };
            OpDiff {
                op_type: op.to_string(),
                count_before: b,
                count_after: a,
                delta,
                change,
            }
        })
        .collect();

    op_diffs.sort_by(|a, b| b.delta.abs().cmp(&a.delta.abs()));

    let added: Vec<&str> = op_diffs
        .iter()
        .filter(|d| d.change == DiffChange::Added)
        .map(|d| d.op_type.as_str())
        .collect();
    let removed: Vec<&str> = op_diffs
        .iter()
        .filter(|d| d.change == DiffChange::Removed)
        .map(|d| d.op_type.as_str())
        .collect();

    let summary = if added.is_empty() && removed.is_empty() {
        format!(
            "No structural changes between runs ({} → {} links).",
            before.len(),
            after.len()
        )
    } else {
        let mut parts = vec![];
        if !added.is_empty() {
            parts.push(format!("New operations: {}", added.join(", ")));
        }
        if !removed.is_empty() {
            parts.push(format!("Removed operations: {}", removed.join(", ")));
        }
        parts.join("; ")
    };

    RunDiff {
        op_diffs,
        total_before: before.len(),
        total_after: after.len(),
        summary,
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn mk(kind: &str) -> serde_json::Value {
        json!({"matched_fields": {"candidate_type": kind}})
    }

    #[test]
    fn test_detects_added_operation() {
        let before = vec![mk("file_read")];
        let after = vec![mk("file_read"), mk("network_connect")];
        let diff = diff_runs(&before, &after);
        assert!(
            diff.op_diffs
                .iter()
                .any(|d| d.op_type == "network_connect" && d.change == DiffChange::Added)
        );
    }

    #[test]
    fn test_detects_removed_operation() {
        let before = vec![mk("file_read"), mk("process_exec")];
        let after = vec![mk("file_read")];
        let diff = diff_runs(&before, &after);
        assert!(
            diff.op_diffs
                .iter()
                .any(|d| d.op_type == "process_exec" && d.change == DiffChange::Removed)
        );
    }

    #[test]
    fn test_no_change_produces_unchanged() {
        let links = vec![mk("file_read")];
        let diff = diff_runs(&links, &links);
        assert!(
            diff.op_diffs
                .iter()
                .all(|d| d.change == DiffChange::Unchanged)
        );
    }

    #[test]
    fn test_detects_increased_operation() {
        let before = vec![mk("file_read")];
        let after = vec![mk("file_read"), mk("file_read")];
        let diff = diff_runs(&before, &after);
        assert!(
            diff.op_diffs
                .iter()
                .any(|d| d.op_type == "file_read" && d.change == DiffChange::Increased)
        );
    }

    #[test]
    fn test_detects_decreased_operation() {
        let before = vec![mk("file_read"), mk("file_read"), mk("file_read")];
        let after = vec![mk("file_read")];
        let diff = diff_runs(&before, &after);
        assert!(
            diff.op_diffs
                .iter()
                .any(|d| d.op_type == "file_read" && d.change == DiffChange::Decreased)
        );
    }

    #[test]
    fn test_diff_runs_typed_with_action_types() {
        use crate::responsibility::types::{
            ActionId, CandidateDerivationMode, EvidenceId, IntentId, MatchLevel, NormalizedArgs,
            PhaseIndicator,
        };

        let make_ev = |evidence_id: &str, action_id: &str| -> CorrelationEvidence {
            CorrelationEvidence {
                evidence_id: EvidenceId::from(evidence_id),
                intent_id: IntentId::from("int-1"),
                action_id: ActionId::from(action_id),
                match_level: MatchLevel::L1Exact,
                confidence: 0.9,
                matched_fields: NormalizedArgs {
                    canonical_path: None,
                    host: None,
                    port: None,
                    command: None,
                    raw_args: Vec::new(),
                },
                derivation_mode: CandidateDerivationMode::Direct,
                phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
                timestamp_ns: 1000,
            }
        };

        let before = vec![make_ev("ev-1", "act-1"), make_ev("ev-2", "act-2")];
        let after = vec![
            make_ev("ev-3", "act-1"),
            make_ev("ev-4", "act-3"),
            make_ev("ev-5", "act-3"),
        ];

        let mut action_types = HashMap::new();
        action_types.insert("act-1".to_string(), "FileRead".to_string());
        action_types.insert("act-2".to_string(), "FileWrite".to_string());
        action_types.insert("act-3".to_string(), "NetworkConnect".to_string());

        let diff = diff_runs_typed(&before, &after, &action_types);

        // FileRead: 1 → 1, unchanged
        // FileWrite: 1 → 0, removed
        // NetworkConnect: 0 → 2, added
        assert_eq!(diff.total_before, 2);
        assert_eq!(diff.total_after, 3);

        let filewrite = diff
            .op_diffs
            .iter()
            .find(|d| d.op_type == "FileWrite")
            .unwrap();
        assert_eq!(filewrite.change, DiffChange::Removed);
        assert_eq!(filewrite.count_before, 1);
        assert_eq!(filewrite.count_after, 0);

        let network = diff
            .op_diffs
            .iter()
            .find(|d| d.op_type == "NetworkConnect")
            .unwrap();
        assert_eq!(network.change, DiffChange::Added);
        assert_eq!(network.count_before, 0);
        assert_eq!(network.count_after, 2);
    }

    #[test]
    fn test_empty_both_runs_unchanged() {
        let diff = diff_runs(&[], &[]);
        assert!(diff.op_diffs.is_empty());
        assert_eq!(diff.total_before, 0);
        assert_eq!(diff.total_after, 0);
    }

    #[test]
    fn test_summary_includes_added_and_removed() {
        let before = vec![mk("process_exec")];
        let after = vec![mk("network_connect")];
        let diff = diff_runs(&before, &after);
        assert!(diff.summary.contains("New operations"));
        assert!(diff.summary.contains("Removed operations"));
    }
}
