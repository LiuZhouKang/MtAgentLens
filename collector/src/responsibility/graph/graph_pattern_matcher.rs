/// Innovation 17: Subgraph isomorphism / violation pattern matching over causal links.
///
/// Instead of full VF2 (too expensive on hot path), we use ordered sequence matching
/// over CorrelationEvidence streams: a ViolationTemplate is a sequence of NodeConstraints that
/// must appear in order within a time window for the same agent.
use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::responsibility::evidence::correlator_legacy::{CorrelationEvidence, MatchLevel};

// ── Template definition ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeConstraint {
    /// Matches CorrelationEvidence candidate_type / intent_class substring
    pub kind_contains: Option<String>,
    /// Matches CorrelationEvidence matched_fields["target"] or candidate path substring
    pub target_contains: Option<String>,
    /// Minimum match level (l1 < l2 < … l5); None = any
    pub min_match_level: Option<u8>,
}

impl NodeConstraint {
    fn matches(&self, link: &CorrelationEvidence) -> bool {
        if let Some(k) = &self.kind_contains {
            let matched_fields = link.matched_fields.as_ref();
            let candidate_type = matched_fields
                .and_then(|f| f.get("candidate_type"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let boundary_id = matched_fields
                .and_then(|f| f.get("boundary_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let policy_id = matched_fields
                .and_then(|f| f.get("policy_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let semantic_haystack = format!(
                "policy:{policy_id}|boundary:{boundary_id}|candidate_type:{candidate_type}"
            );
            if !semantic_haystack.contains(k.as_str()) && !candidate_type.contains(k.as_str()) {
                return false;
            }
        }
        if let Some(t) = &self.target_contains {
            let haystack = link
                .matched_fields
                .as_ref()
                .and_then(|f| f.get("target"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if !haystack.contains(t.as_str()) {
                return false;
            }
        }
        if let Some(min) = self.min_match_level {
            let level = match_level_ordinal(&link.match_level);
            if level > min {
                // higher ordinal = weaker match
                return false;
            }
        }
        true
    }
}

fn match_level_ordinal(ml: &MatchLevel) -> u8 {
    use MatchLevel::*;
    match ml {
        L1Exact => 1,
        L2Basename => 2,
        L3Host => 3,
        L4Argv0 => 4,
        L5Substring => 5,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ViolationTemplate {
    pub template_id: String,
    pub description: String,
    /// Ordered sequence of constraints; all must match in order within window_ns
    pub sequence: Vec<NodeConstraint>,
    /// Max nanoseconds between first and last matched link
    pub window_ns: u64,
    pub severity: String,
}

// ── Match result ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PatternMatch {
    pub template_id: String,
    pub agent_id: String,
    pub matched_evidence_ids: Vec<String>,
    pub matched_policies: Vec<String>,
    pub matched_boundaries: Vec<String>,
    pub matched_targets: Vec<String>,
    pub start_ns: u64,
    pub end_ns: u64,
    pub description: String,
    pub severity: String,
}

// ── Matcher ───────────────────────────────────────────────────────────────────

pub struct PatternMatcher {
    templates: Vec<ViolationTemplate>,
    /// Per-agent sliding window of recent links
    window: HashMap<String, Vec<CorrelationEvidence>>,
    /// Prune links older than this (max window across all templates)
    max_window_ns: u64,
}

impl PatternMatcher {
    pub fn new(templates: Vec<ViolationTemplate>) -> Self {
        let max_window_ns = templates.iter().map(|t| t.window_ns).max().unwrap_or(0);
        Self {
            templates,
            window: HashMap::new(),
            max_window_ns,
        }
    }

    /// Feed a new causal link; returns any newly triggered pattern matches.
    pub fn on_link(&mut self, link: CorrelationEvidence) -> Vec<PatternMatch> {
        let agent = link.agent_id.clone();
        let ts = link.timestamp_ns;

        let buf = self.window.entry(agent.clone()).or_default();
        buf.push(link);

        // Prune old links
        let cutoff = ts.saturating_sub(self.max_window_ns);
        buf.retain(|l| l.timestamp_ns >= cutoff);

        let buf_snapshot = buf.clone();
        let mut matches = Vec::new();

        for tmpl in &self.templates {
            if let Some(m) = self.try_match(tmpl, &agent, &buf_snapshot) {
                matches.push(m);
            }
        }
        matches
    }

    fn try_match(
        &self,
        tmpl: &ViolationTemplate,
        agent_id: &str,
        buf: &[CorrelationEvidence],
    ) -> Option<PatternMatch> {
        if tmpl.sequence.is_empty() || buf.is_empty() {
            return None;
        }

        for start_idx in 0..buf.len() {
            if !tmpl.sequence[0].matches(&buf[start_idx]) {
                continue;
            }

            let mut seq_idx = 1;
            let mut matched: Vec<&CorrelationEvidence> = vec![&buf[start_idx]];

            for link in &buf[(start_idx + 1)..] {
                if seq_idx < tmpl.sequence.len() && tmpl.sequence[seq_idx].matches(link) {
                    matched.push(link);
                    seq_idx += 1;
                    if seq_idx == tmpl.sequence.len() {
                        break;
                    }
                }
            }

            if matched.len() < tmpl.sequence.len() {
                continue;
            }

            let start_ns = matched.first().unwrap().timestamp_ns;
            let end_ns = matched.last().unwrap().timestamp_ns;
            if end_ns - start_ns > tmpl.window_ns {
                continue;
            }

            return Some(PatternMatch {
                template_id: tmpl.template_id.clone(),
                agent_id: agent_id.to_string(),
                matched_evidence_ids: matched.iter().map(|l| l.evidence_id.clone()).collect(),
                matched_policies: matched
                    .iter()
                    .filter_map(|l| {
                        l.matched_fields
                            .as_ref()
                            .and_then(|f| f.get("policy_id"))
                            .and_then(|v| v.as_str())
                            .map(|v| v.to_string())
                    })
                    .collect(),
                matched_boundaries: matched
                    .iter()
                    .filter_map(|l| {
                        l.matched_fields
                            .as_ref()
                            .and_then(|f| f.get("boundary_id"))
                            .and_then(|v| v.as_str())
                            .map(|v| v.to_string())
                    })
                    .collect(),
                matched_targets: matched
                    .iter()
                    .filter_map(|l| {
                        l.matched_fields
                            .as_ref()
                            .and_then(|f| f.get("target"))
                            .and_then(|v| v.as_str())
                            .map(|v| v.to_string())
                    })
                    .collect(),
                start_ns,
                end_ns,
                description: tmpl.description.clone(),
                severity: tmpl.severity.clone(),
            });
        }

        None
    }
}

// ── Built-in templates ────────────────────────────────────────────────────────

/// Returns the default set of violation templates covering known multi-agent attack patterns.
pub fn default_templates() -> Vec<ViolationTemplate> {
    vec![
        ViolationTemplate {
            template_id: "shared_artifact_read_then_exec".to_string(),
            description: "Agent read a shared artifact then executed a process — possible trust propagation exploit".to_string(),
            sequence: vec![
                NodeConstraint {
                    kind_contains: Some("policy:policy.file.read|boundary:boundary.file_read".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
                NodeConstraint {
                    kind_contains: Some("policy:policy.process.exec|boundary:boundary.process_exec".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
            ],
            window_ns: 60_000_000_000, // 60 s
            severity: "warning".to_string(),
        },
        ViolationTemplate {
            template_id: "prompt_injection_then_network".to_string(),
            description: "Agent received a tool_call after reading an injected file, then made a network connection".to_string(),
            sequence: vec![
                NodeConstraint {
                    kind_contains: Some("policy:policy.file.read|boundary:boundary.file_read".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
                NodeConstraint {
                    kind_contains: Some("policy:policy.network.connect|boundary:boundary.network_connect".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
            ],
            window_ns: 120_000_000_000, // 2 min
            severity: "critical".to_string(),
        },
        ViolationTemplate {
            template_id: "privilege_escalation_attempt".to_string(),
            description: "Agent read a sensitive path then attempted a file write — possible privilege escalation".to_string(),
            sequence: vec![
                NodeConstraint {
                    kind_contains: Some("policy:policy.file.read|boundary:boundary.file_read".to_string()),
                    target_contains: Some("/etc/".to_string()),
                    min_match_level: None,
                },
                NodeConstraint {
                    kind_contains: Some("policy:policy.file.write|boundary:boundary.file_write".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
            ],
            window_ns: 30_000_000_000, // 30 s
            severity: "critical".to_string(),
        },
        ViolationTemplate {
            template_id: "reasoning_loop_exec_cycle".to_string(),
            description: "Agent executed the same process type twice in a short window — possible reasoning loop".to_string(),
            sequence: vec![
                NodeConstraint {
                    kind_contains: Some("process_exec".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
                NodeConstraint {
                    kind_contains: Some("process_exec".to_string()),
                    target_contains: None,
                    min_match_level: None,
                },
            ],
            window_ns: 10_000_000_000, // 10 s
            severity: "info".to_string(),
        },
    ]
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::evidence::correlator_legacy::MatchLevel;
    use serde_json::json;

    fn mk_link(id: &str, agent: &str, ts: u64, kind: &str, target: &str) -> CorrelationEvidence {
        CorrelationEvidence {
            schema_version: 1,
            evidence_id: id.to_string(),
            agent_id: agent.to_string(),
            intent_id: "i1".to_string(),
            candidate_id: "c1".to_string(),
            timestamp_ns: ts,
            confidence: 0.9,
            match_level: MatchLevel::L1Exact,
            matched_fields: Some(json!({
                "candidate_type": kind,
                "target": target,
            })),
            supporting_raw_event_refs: None,
            conversation_id: None,
            authority_source: None,
            message_id: None,
            source_event_ref: None,
            debug_notes: None,
            derivation_mode: None,
        }
    }

    #[test]
    fn detects_shared_artifact_read_then_exec() {
        let mut matcher = PatternMatcher::new(default_templates());
        let mut read = mk_link(
            "l1",
            "agent-a",
            1_000,
            "legacy_irrelevant",
            "/shared/policy.md",
        );
        read.matched_fields = Some(json!({
            "candidate_type": "legacy_irrelevant",
            "target": "/shared/policy.md",
            "boundary_id": "boundary.file_read",
            "policy_id": "policy.file.read"
        }));
        let r = matcher.on_link(read);
        assert!(r.is_empty());
        let mut exec = mk_link(
            "l2",
            "agent-a",
            2_000,
            "legacy_irrelevant_exec",
            "/bin/bash",
        );
        exec.matched_fields = Some(json!({
            "candidate_type": "legacy_irrelevant_exec",
            "target": "/bin/bash",
            "boundary_id": "boundary.process_exec",
            "policy_id": "policy.process.exec"
        }));
        let r = matcher.on_link(exec);
        assert!(
            r.iter()
                .any(|m| m.template_id == "shared_artifact_read_then_exec"),
            "default shared-artifact template should match on stable policy/boundary semantics, not legacy candidate_type strings"
        );
    }

    #[test]
    fn no_match_across_agents() {
        let mut matcher = PatternMatcher::new(default_templates());
        matcher.on_link(mk_link(
            "l1",
            "agent-a",
            1_000,
            "file_read",
            "/shared/policy.md",
        ));
        let r = matcher.on_link(mk_link("l2", "agent-b", 2_000, "process_exec", "/bin/bash"));
        assert!(
            !r.iter()
                .any(|m| m.template_id == "shared_artifact_read_then_exec")
        );
    }

    #[test]
    fn no_match_outside_window() {
        let mut matcher = PatternMatcher::new(default_templates());
        matcher.on_link(mk_link(
            "l1",
            "agent-a",
            0,
            "file_read",
            "/shared/policy.md",
        ));
        // 61 seconds later — outside 60s window
        let r = matcher.on_link(mk_link(
            "l2",
            "agent-a",
            61_000_000_001,
            "process_exec",
            "/bin/bash",
        ));
        assert!(
            !r.iter()
                .any(|m| m.template_id == "shared_artifact_read_then_exec")
        );
    }

    #[test]
    fn reasoning_loop_exec_cycle_matches_latest_valid_window_not_oldest_exec_pair() {
        let mut matcher = PatternMatcher::new(default_templates());
        matcher.on_link(mk_link("l1", "agent-a", 0, "process_exec", "/usr/bin/curl"));
        matcher.on_link(mk_link(
            "l2",
            "agent-a",
            31_000_000_000,
            "process_exec",
            "/usr/bin/curl",
        ));
        let r = matcher.on_link(mk_link(
            "l3",
            "agent-a",
            39_000_000_000,
            "process_exec",
            "/usr/bin/curl",
        ));
        assert!(
            r.iter()
                .any(|m| m.template_id == "reasoning_loop_exec_cycle"),
            "matcher should not get stuck on the oldest out-of-window exec pair when a newer valid pair exists"
        );
    }

    #[test]
    fn detects_privilege_escalation_attempt() {
        let mut matcher = PatternMatcher::new(default_templates());
        let mut read = mk_link("l1", "agent-a", 1_000, "file_read", "/etc/shadow");
        read.matched_fields = Some(json!({
            "candidate_type": "file_read",
            "target": "/etc/shadow",
            "boundary_id": "boundary.file_read",
            "policy_id": "policy.file.read"
        }));
        matcher.on_link(read);
        let mut write = mk_link("l2", "agent-a", 5_000, "file_write", "/etc/shadow");
        write.matched_fields = Some(json!({
            "candidate_type": "file_write",
            "target": "/etc/shadow",
            "boundary_id": "boundary.file_write",
            "policy_id": "policy.file.write"
        }));
        let r = matcher.on_link(write);
        assert!(
            r.iter()
                .any(|m| m.template_id == "privilege_escalation_attempt")
        );
    }

    #[test]
    fn policy_violation_template_matches_on_boundary_and_policy_semantics() {
        let template = ViolationTemplate {
            template_id: "policy_file_read_then_exec".to_string(),
            description: "read boundary followed by exec boundary".to_string(),
            sequence: vec![
                NodeConstraint {
                    kind_contains: Some(
                        "policy:policy.file.read|boundary:boundary.file_read".to_string(),
                    ),
                    target_contains: None,
                    min_match_level: None,
                },
                NodeConstraint {
                    kind_contains: Some(
                        "policy:policy.process.exec|boundary:boundary.process_exec".to_string(),
                    ),
                    target_contains: None,
                    min_match_level: None,
                },
            ],
            window_ns: 10_000,
            severity: "critical".to_string(),
        };
        let mut matcher = PatternMatcher::new(vec![template]);

        let mut read = mk_link(
            "l1",
            "agent-a",
            1_000,
            "mismatched_legacy_kind",
            "/tmp/shared.txt",
        );
        read.matched_fields = Some(json!({
            "candidate_type": "mismatched_legacy_kind",
            "target": "/tmp/shared.txt",
            "boundary_id": "boundary.file_read",
            "policy_id": "policy.file.read"
        }));
        let mut exec = mk_link(
            "l2",
            "agent-a",
            2_000,
            "another_legacy_kind",
            "/usr/bin/bash",
        );
        exec.matched_fields = Some(json!({
            "candidate_type": "another_legacy_kind",
            "target": "/usr/bin/bash",
            "boundary_id": "boundary.process_exec",
            "policy_id": "policy.process.exec"
        }));

        let r1 = matcher.on_link(read);
        assert!(r1.is_empty());
        let r2 = matcher.on_link(exec);
        assert!(
            r2.iter()
                .any(|m| m.template_id == "policy_file_read_then_exec"),
            "policy-aware template should match on stable policy/boundary semantics even when legacy candidate_type strings do not match"
        );
    }

    #[test]
    fn default_templates_use_policy_boundary_semantics() {
        let templates = default_templates();
        let by_id: std::collections::HashMap<_, _> = templates
            .iter()
            .map(|template| (template.template_id.as_str(), template))
            .collect();

        let shared = by_id
            .get("shared_artifact_read_then_exec")
            .expect("shared template");
        assert_eq!(
            shared.sequence[0].kind_contains.as_deref(),
            Some("policy:policy.file.read|boundary:boundary.file_read")
        );
        assert_eq!(
            shared.sequence[1].kind_contains.as_deref(),
            Some("policy:policy.process.exec|boundary:boundary.process_exec")
        );

        let network = by_id
            .get("prompt_injection_then_network")
            .expect("network template");
        assert_eq!(
            network.sequence[0].kind_contains.as_deref(),
            Some("policy:policy.file.read|boundary:boundary.file_read")
        );
        assert_eq!(
            network.sequence[1].kind_contains.as_deref(),
            Some("policy:policy.network.connect|boundary:boundary.network_connect")
        );

        let privilege = by_id
            .get("privilege_escalation_attempt")
            .expect("privilege template");
        assert_eq!(
            privilege.sequence[0].kind_contains.as_deref(),
            Some("policy:policy.file.read|boundary:boundary.file_read")
        );
        assert_eq!(
            privilege.sequence[1].kind_contains.as_deref(),
            Some("policy:policy.file.write|boundary:boundary.file_write")
        );
    }
}
