/// Innovation 8: Cross-run causal graph diff analysis.
///
/// Compares two sets of causal links (from different runs) and produces a diff
/// showing which operation types appeared, disappeared, or changed in frequency.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpDiff {
    pub op_type: String,
    pub count_before: usize,
    pub count_after: usize,
    pub delta: i64,
    pub change: DiffChange,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum DiffChange {
    Added,
    Removed,
    Increased,
    Decreased,
    Unchanged,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunDiff {
    pub op_diffs: Vec<OpDiff>,
    pub total_before: usize,
    pub total_after: usize,
    pub summary: String,
}

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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn mk(kind: &str) -> serde_json::Value {
        json!({"matched_fields": {"candidate_type": kind}})
    }

    #[test]
    fn detects_added_operation() {
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
    fn detects_removed_operation() {
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
    fn no_change_produces_unchanged() {
        let links = vec![mk("file_read")];
        let diff = diff_runs(&links, &links);
        assert!(
            diff.op_diffs
                .iter()
                .all(|d| d.change == DiffChange::Unchanged)
        );
    }
}
