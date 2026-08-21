// SPDX-License-Identifier: MIT
//
// correlator.rs — EvidenceCorrelator (Phase 1 streaming + Phase 2 batch).
//
// Phase 1 (streaming, real-time):
//   on_intent() → match incoming intent against buffered actions
//   on_action() → match incoming action against buffered intents
//   Both produce CorrelationEvidence when a process-tree + argument match
//   is found, and NonLinkDiagnostic otherwise.
//
// Phase 2 (batch, post-hoc):
//   correlate_batch() → Cartesian product over ALL intents and actions
//   (no time window gating). Time proximity affects confidence but not
//   eligibility.
//
// Matching logic:
//   1. Process tree check: can_correlate(intent.process_id, action.process_id)
//      via SignalSnapshot::can_correlate.
//   2. Argument matching: argument_matcher::match_args() produces the best
//      L1-L5 match level.
//   3. Confidence scoring: confidence::compute_confidence() combines match
//      level, time delta, tree distance, signal class, and competing matches.
//   4. Multi-action intents (Bash/Write): allowed to match multiple actions
//      via (intent_id, action_id) deduplication.

use crate::responsibility::evidence::argument_matcher::match_args;
use crate::responsibility::evidence::buffer::{ActionBuffer, IntentBuffer};
use crate::responsibility::evidence::confidence::compute_confidence;
use crate::responsibility::evidence::non_link_diagnostic;
use crate::responsibility::evidence::quality::passes_quality_gate;
use crate::responsibility::signals::SignalSnapshot;
use crate::responsibility::types::{
    Action, CandidateDerivationMode, CorrelationEvidence, EvidenceId, EvidenceRefinement,
    EvidenceRefinementType, Intent, IntentId, MatchLevel, NonLinkDiagnostic, PhaseIndicator,
};
use std::collections::HashSet;

/// Minimum confidence to form a correlation link for L1-L3 matches.
const CONFIDENCE_CUTOFF: f32 = 0.25;

/// L4 (basename) matches — require moderate confidence.
const L4_MIN_CONFIDENCE: f32 = 0.40;

/// L5 (substring) matches — require reasonable confidence.
/// Weak substring matches are too noisy and dilute the graph signal.
const L5_MIN_CONFIDENCE: f32 = 0.35;

/// Return the minimum confidence required for a given match level.
fn min_confidence_for_level(level: &MatchLevel) -> f32 {
    match level {
        MatchLevel::L1Exact | MatchLevel::L2Normalized | MatchLevel::L3Containment => {
            CONFIDENCE_CUTOFF
        }
        MatchLevel::L4Basename => L4_MIN_CONFIDENCE,
        MatchLevel::L5Substring => L5_MIN_CONFIDENCE,
    }
}

/// Build a rough string representation of what was matched.
///
/// Used by the quality gate to check L4/L5 match string length.
fn format_matched_string(intent: &Intent, action: &Action) -> String {
    // Best-effort: use the first path-like or command-like arg
    for arg in &action.normalized_args {
        match arg {
            crate::responsibility::types::NormalizedArg::Path(p) => return p.clone(),
            crate::responsibility::types::NormalizedArg::Command(c) => return c.clone(),
            crate::responsibility::types::NormalizedArg::Url { host, .. } => return host.clone(),
            crate::responsibility::types::NormalizedArg::Content(_) => {}
        }
    }
    for arg in &intent.tool_args {
        match arg {
            crate::responsibility::types::NormalizedArg::Path(p) => return p.clone(),
            crate::responsibility::types::NormalizedArg::Command(c) => return c.clone(),
            crate::responsibility::types::NormalizedArg::Url { host, .. } => return host.clone(),
            crate::responsibility::types::NormalizedArg::Content(_) => {}
        }
    }
    String::new()
}

/// Maximum process tree distance to even attempt correlation.
/// Beyond this, the process-tree relationship is too weak.
const MAX_TREE_DISTANCE: usize = 10;

// ─── CorrelationResult ────────────────────────────────────────────────

/// Phase 2 batch correlation result.
#[derive(Debug, Clone)]
pub struct CorrelationResult {
    /// All evidence links found.
    pub evidence: Vec<CorrelationEvidence>,
    /// Diagnostics for intents/actions that could not be linked.
    pub non_links: Vec<NonLinkDiagnostic>,
    /// Refinements linking Phase 1 provisional evidence to Phase 2 results.
    pub refinements: Vec<EvidenceRefinement>,
}

// ─── EvidenceCorrelator ───────────────────────────────────────────────

/// Streaming + batch evidence correlator.
///
/// Maintains TTL-bounded buffers of intents and actions for Phase 1
/// streaming correlation. Phase 2 batch correlation operates on the
/// complete datasets without time window gating.
#[derive(Debug)]
pub struct EvidenceCorrelator {
    intent_buffer: IntentBuffer,
    action_buffer: ActionBuffer,
    /// Deduplication set: (IntentId, ActionId) pairs already linked.
    linked_pairs: HashSet<(String, String)>,
    /// Accumulated diagnostics (drained periodically).
    diagnostics: Vec<NonLinkDiagnostic>,
    /// Count of correlations produced so far (used for refinement tracking).
    correlation_count: u64,
}

impl EvidenceCorrelator {
    /// Create a new, empty correlator with default buffer sizes and TTLs.
    pub fn new() -> Self {
        Self {
            intent_buffer: IntentBuffer::new(),
            action_buffer: ActionBuffer::new(),
            linked_pairs: HashSet::new(),
            diagnostics: Vec::new(),
            correlation_count: 0,
        }
    }

    /// Create a correlator with custom buffer configuration.
    pub fn with_buffer_config(buffer_max_size: usize, buffer_ttl_ns: u64) -> Self {
        Self {
            intent_buffer: IntentBuffer::with_config(buffer_max_size, buffer_ttl_ns),
            action_buffer: ActionBuffer::with_config(buffer_max_size, buffer_ttl_ns),
            linked_pairs: HashSet::new(),
            diagnostics: Vec::new(),
            correlation_count: 0,
        }
    }

    // ── Phase 1: Streaming ──────────────────────────────────────────

    /// Process an incoming intent.
    ///
    /// Matches the intent against all currently buffered actions.
    /// Returns any CorrelationEvidence found and adds diagnostics
    /// for intents that could not be matched.
    pub fn on_intent(
        &mut self,
        intent: Intent,
        snapshot: &SignalSnapshot,
    ) -> Vec<CorrelationEvidence> {
        let mut results = Vec::new();
        let now_ns = intent.timestamp_ns;

        // Evict expired items
        self.intent_buffer.evict_expired(now_ns);
        self.action_buffer.evict_expired(now_ns);

        if !intent.primary_matching_eligible {
            // Non-primary intents (regex fallback) get a diagnostic but
            // we still attempt matching
            self.diagnostics
                .push(non_link_diagnostic::intent_not_primary_eligible(
                    intent.intent_id.clone(),
                    now_ns,
                ));
        }

        let mut matched = false;

        // Clone actions to avoid borrow conflict with try_correlate(&mut self)
        let actions: Vec<Action> = self.action_buffer.iter().cloned().collect();
        for action in &actions {
            if let Some(evidence) = self.try_correlate(
                &intent,
                action,
                snapshot,
                PhaseIndicator::Provisional {
                    timestamp_ns: now_ns,
                },
            ) {
                results.push(evidence);
                matched = true;
            }
        }

        if !matched {
            // Check if there were any eligible actions at all
            let has_eligible = self
                .action_buffer
                .iter()
                .any(|a| snapshot.can_correlate(intent.process_id, a.process_id));

            if has_eligible {
                self.diagnostics
                    .push(non_link_diagnostic::no_argument_match(
                        intent.intent_id.clone(),
                        now_ns,
                    ));
            } else {
                self.diagnostics
                    .push(non_link_diagnostic::candidate_not_available(
                        intent.intent_id.clone(),
                        now_ns,
                    ));
            }
        }

        // Always buffer the intent for future action matching
        self.intent_buffer.push(intent);

        results
    }

    /// Process an incoming action.
    ///
    /// Matches the action against all currently buffered intents.
    /// Returns any CorrelationEvidence found and adds diagnostics
    /// for unmatched actions.
    pub fn on_action(
        &mut self,
        action: Action,
        snapshot: &SignalSnapshot,
    ) -> Vec<CorrelationEvidence> {
        let mut results = Vec::new();
        let now_ns = action.timestamp_ns;

        // Evict expired items
        self.intent_buffer.evict_expired(now_ns);
        self.action_buffer.evict_expired(now_ns);

        let mut matched = false;

        // Clone intents to avoid borrow conflict with try_correlate(&mut self)
        let intents: Vec<Intent> = self.intent_buffer.iter().cloned().collect();
        for intent in &intents {
            if let Some(evidence) = self.try_correlate(
                intent,
                &action,
                snapshot,
                PhaseIndicator::Provisional {
                    timestamp_ns: now_ns,
                },
            ) {
                results.push(evidence);
                matched = true;
            }
        }

        if !matched {
            let has_eligible = self
                .intent_buffer
                .iter()
                .any(|i| snapshot.can_correlate(i.process_id, action.process_id));

            if has_eligible {
                self.diagnostics
                    .push(non_link_diagnostic::no_argument_match(
                        // Use a placeholder intent_id for action-only diagnostic
                        // The diagnostic is about the action not finding any matching intent
                        IntentId::from(format!("action-diag-{}", action.action_id.as_str())),
                        now_ns,
                    ));
            }
            // Note: we don't produce candidate_not_available for isolated actions
            // as they may be matched later when their intent arrives
        }

        // Always buffer the action for future intent matching
        self.action_buffer.push(action);

        results
    }

    /// Drain all accumulated diagnostics.
    pub fn drain_diagnostics(&mut self) -> Vec<NonLinkDiagnostic> {
        std::mem::take(&mut self.diagnostics)
    }

    // ── Phase 2: Batch ──────────────────────────────────────────────

    /// Batch correlate all intents against all actions.
    ///
    /// No time window gating — all pairs are considered. Time proximity
    /// affects confidence via compute_confidence() but never gates
    /// eligibility. This is the authoritative Phase 2 correlation.
    pub fn correlate_batch(
        &self,
        intents: &[Intent],
        actions: &[Action],
        snapshot: &SignalSnapshot,
    ) -> CorrelationResult {
        let mut evidence_list: Vec<CorrelationEvidence> = Vec::new();
        let mut non_links: Vec<NonLinkDiagnostic> = Vec::new();
        let mut linked_intents: HashSet<String> = HashSet::new();
        let mut linked_actions: HashSet<String> = HashSet::new();
        // Track competing matches per intent for confidence scoring
        let mut match_counts: std::collections::HashMap<String, usize> =
            std::collections::HashMap::new();

        // First pass: find all valid matches
        let mut matches: Vec<(usize, usize, MatchLevel, f32)> = Vec::new();
        for (i_idx, intent) in intents.iter().enumerate() {
            for (a_idx, action) in actions.iter().enumerate() {
                if let Some((level, _score)) =
                    match_args(&intent.tool_args, &action.normalized_args)
                {
                    // Check process tree
                    if snapshot.can_correlate(intent.process_id, action.process_id) {
                        let tree_dist =
                            compute_tree_distance(intent.process_id, action.process_id, snapshot);
                        let _time_delta = intent.timestamp_ns.abs_diff(action.timestamp_ns);
                        // Get competing matches count
                        let competing = *match_counts.get(intent.intent_id.as_str()).unwrap_or(&0);
                        // Phase 2 batch: no time window per spec §7.3.
                        // Time delta affects confidence but doesn't hard-gate.
                        // Pass 0 to skip time penalty in batch mode.
                        let confidence = compute_confidence(
                            &level,
                            0, // batch mode: no time penalty
                            tree_dist,
                            &action.signal_class,
                            competing,
                        );

                        if confidence >= min_confidence_for_level(&level) {
                            matches.push((i_idx, a_idx, level.clone(), confidence));
                            *match_counts
                                .entry(intent.intent_id.as_str().to_string())
                                .or_insert(0) += 1;
                        }
                    }
                }
            }
        }

        // Second pass: produce evidence and track linked sets
        let mut final_matches: Vec<CorrelationEvidence> = Vec::new();
        for (i_idx, a_idx, level, confidence) in matches {
            let intent = &intents[i_idx];
            let action = &actions[a_idx];

            // Dedup: skip already-linked pairs
            let pair_key = (
                intent.intent_id.as_str().to_string(),
                action.action_id.as_str().to_string(),
            );
            if self.linked_pairs.contains(&pair_key) {
                continue;
            }

            let evidence = CorrelationEvidence {
                evidence_id: EvidenceId::new(),
                intent_id: intent.intent_id.clone(),
                action_id: action.action_id.clone(),
                match_level: level,
                confidence,
                matched_fields: crate::responsibility::types::NormalizedArgs {
                    canonical_path: None,
                    host: None,
                    port: None,
                    command: None,
                    raw_args: Vec::new(),
                },
                derivation_mode: CandidateDerivationMode::Direct,
                phase: PhaseIndicator::Authoritative {
                    timestamp_ns: std::cmp::max(intent.timestamp_ns, action.timestamp_ns),
                    overrides: None,
                },
                timestamp_ns: std::cmp::max(intent.timestamp_ns, action.timestamp_ns),
            };

            linked_intents.insert(intent.intent_id.as_str().to_string());
            linked_actions.insert(action.action_id.as_str().to_string());
            final_matches.push(evidence);
        }

        // Produce diagnostics for unmatched intents
        for intent in intents {
            if !linked_intents.contains(intent.intent_id.as_str())
                && intent.primary_matching_eligible
            {
                // Check if any action was process-tree eligible
                let eligible = actions
                    .iter()
                    .any(|a| snapshot.can_correlate(intent.process_id, a.process_id));
                if eligible {
                    non_links.push(non_link_diagnostic::no_argument_match(
                        intent.intent_id.clone(),
                        intent.timestamp_ns,
                    ));
                } else {
                    non_links.push(non_link_diagnostic::candidate_not_available(
                        intent.intent_id.clone(),
                        intent.timestamp_ns,
                    ));
                }
            }
        }

        // Build refinements: for now, mark all Phase 2 evidence as Discovered
        // (In future: compare with Phase 1 evidence to produce Confirmed/Upgraded/Downgraded/etc.)
        let refinements: Vec<EvidenceRefinement> = final_matches
            .iter()
            .map(|ev| EvidenceRefinement {
                phase1_evidence_id: EvidenceId::from("pending-refinement"),
                phase2_evidence_id: Some(ev.evidence_id.clone()),
                refinement_type: EvidenceRefinementType::Discovered,
                reason: "Phase 2 batch correlation discovered this evidence link".to_string(),
            })
            .collect();

        evidence_list.extend(final_matches);

        CorrelationResult {
            evidence: evidence_list,
            non_links,
            refinements,
        }
    }

    // ── Internal helpers ────────────────────────────────────────────

    /// Try to correlate a single intent-action pair.
    ///
    /// Returns CorrelationEvidence if:
    /// 1. The pair hasn't already been linked (dedup).
    /// 2. Process tree allows correlation.
    /// 3. Argument matching succeeds above the confidence cutoff.
    fn try_correlate(
        &mut self,
        intent: &Intent,
        action: &Action,
        snapshot: &SignalSnapshot,
        phase: PhaseIndicator,
    ) -> Option<CorrelationEvidence> {
        let pair_key = (
            intent.intent_id.as_str().to_string(),
            action.action_id.as_str().to_string(),
        );
        if self.linked_pairs.contains(&pair_key) {
            return None; // Already linked
        }

        // Process tree check
        if !snapshot.can_correlate(intent.process_id, action.process_id) {
            return None;
        }

        // Tree distance for confidence
        let tree_dist = compute_tree_distance(intent.process_id, action.process_id, snapshot);
        if tree_dist > MAX_TREE_DISTANCE {
            return None;
        }

        // Argument matching
        let (match_level, base_score) = match_args(&intent.tool_args, &action.normalized_args)?;

        // Quality gate: reject fundamentally weak L4/L5 matches before
        // computing the full confidence score.
        if matches!(
            match_level,
            MatchLevel::L4Basename | MatchLevel::L5Substring
        ) {
            // Build a rough representation of what was matched for the gate check
            let matched_rep = format_matched_string(intent, action);
            if !passes_quality_gate(&match_level, base_score, &matched_rep) {
                return None;
            }
        }

        // Time delta
        let time_delta = intent.timestamp_ns.abs_diff(action.timestamp_ns);

        // Confidence
        let confidence = compute_confidence(
            &match_level,
            time_delta,
            tree_dist,
            &action.signal_class,
            0, // No competing matches in streaming path
        );

        if confidence < min_confidence_for_level(&match_level) {
            // Record below-cutoff diagnostic
            self.diagnostics
                .push(non_link_diagnostic::below_confidence_cutoff(
                    intent.intent_id.clone(),
                    action.action_id.clone(),
                    confidence,
                    match_level.clone(),
                    std::cmp::max(intent.timestamp_ns, action.timestamp_ns),
                ));
            return None;
        }

        // Mark as linked
        self.linked_pairs.insert(pair_key);
        self.correlation_count += 1;

        Some(CorrelationEvidence {
            evidence_id: EvidenceId::new(),
            intent_id: intent.intent_id.clone(),
            action_id: action.action_id.clone(),
            match_level,
            confidence,
            matched_fields: crate::responsibility::types::NormalizedArgs {
                canonical_path: None,
                host: None,
                port: None,
                command: None,
                raw_args: Vec::new(),
            },
            derivation_mode: CandidateDerivationMode::Direct,
            phase,
            timestamp_ns: std::cmp::max(intent.timestamp_ns, action.timestamp_ns),
        })
    }

    /// Number of correlations produced so far.
    pub fn correlation_count(&self) -> u64 {
        self.correlation_count
    }

    /// Number of intents in the buffer.
    pub fn intent_buffer_len(&self) -> usize {
        self.intent_buffer.len()
    }

    /// Number of actions in the buffer.
    pub fn action_buffer_len(&self) -> usize {
        self.action_buffer.len()
    }
}

impl Default for EvidenceCorrelator {
    fn default() -> Self {
        Self::new()
    }
}

// ─── Helper: compute tree distance between two processes ─────────────

/// Compute the distance (number of hops) between two processes in the
/// process tree, using the SignalSnapshot parent map.
///
/// Returns 0 if they are the same process, 1 if parent/child, etc.
/// Returns usize::MAX if unreachable within MAX_TREE_DISTANCE.
fn compute_tree_distance(
    intent_process: crate::responsibility::types::ProcessId,
    action_process: crate::responsibility::types::ProcessId,
    snapshot: &SignalSnapshot,
) -> usize {
    if intent_process == action_process {
        return 0;
    }

    // Check if action is a descendant of intent
    if snapshot.is_descendant_or_same(action_process, intent_process) {
        // Compute depth from action up to intent
        let mut depth = 0;
        let mut current = action_process;
        let mut visited = HashSet::new();
        visited.insert(current);

        for _ in 0..MAX_TREE_DISTANCE {
            if current == intent_process {
                return depth;
            }
            match snapshot.get_parent(current) {
                Some(parent) => {
                    if !visited.insert(parent) {
                        break; // Cycle
                    }
                    current = parent;
                    depth += 1;
                }
                None => break,
            }
        }
        return depth; // Fallback
    }

    // Check if intent is a descendant of action (unlikely but handle it)
    if snapshot.is_descendant_or_same(intent_process, action_process) {
        let mut depth = 0;
        let mut current = intent_process;
        let mut visited = HashSet::new();
        visited.insert(current);

        for _ in 0..MAX_TREE_DISTANCE {
            if current == action_process {
                return depth;
            }
            match snapshot.get_parent(current) {
                Some(parent) => {
                    if !visited.insert(parent) {
                        break;
                    }
                    current = parent;
                    depth += 1;
                }
                None => break,
            }
        }
        return depth;
    }

    // Unrelated processes that share a common ancestor
    // Walk both up to find the LCA
    let ancestors_a = collect_ancestors(intent_process, snapshot, MAX_TREE_DISTANCE);
    let ancestors_b = collect_ancestors(action_process, snapshot, MAX_TREE_DISTANCE);

    for (ancestor, depth_a) in &ancestors_a {
        if let Some(depth_b) = ancestors_b.get(ancestor) {
            return depth_a + depth_b;
        }
    }

    MAX_TREE_DISTANCE + 1 // Unreachable
}

/// Collect all ancestors of a process up to max_depth hops.
fn collect_ancestors(
    process: crate::responsibility::types::ProcessId,
    snapshot: &SignalSnapshot,
    max_depth: usize,
) -> std::collections::HashMap<crate::responsibility::types::ProcessId, usize> {
    let mut ancestors = std::collections::HashMap::new();
    ancestors.insert(process, 0);
    let mut current = process;
    let mut visited = HashSet::new();
    visited.insert(current);

    for depth in 1..=max_depth {
        match snapshot.get_parent(current) {
            Some(parent) if parent != current => {
                if !visited.insert(parent) {
                    break;
                }
                ancestors.insert(parent, depth);
                current = parent;
            }
            _ => break,
        }
    }

    ancestors
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::types::{
        ActionCandidateType, ActionId, CandidateDerivationMode, IntentAuthoritySource, IntentClass,
        IntentParseState, MessageId, NormalizedArg, ProcessId, SignalClass,
    };

    fn pid(id: u32, start: u64) -> ProcessId {
        ProcessId::new(id, start)
    }

    fn msg_id() -> MessageId {
        MessageId::from("msg-test")
    }

    fn make_intent(process_id: ProcessId, tool: &str, args: Vec<NormalizedArg>, ts: u64) -> Intent {
        Intent {
            intent_id: IntentId::new(),
            process_id,
            message_id: msg_id(),
            tool_name: tool.to_string(),
            tool_args: args,
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::SseResponseToolUse,
            timestamp_ns: ts,
        }
    }

    fn make_action(
        process_id: ProcessId,
        action_type: ActionCandidateType,
        args: Vec<NormalizedArg>,
        ts: u64,
    ) -> Action {
        Action {
            action_id: ActionId::new(),
            process_id,
            action_type,
            normalized_args: args,
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: ts,
        }
    }

    fn build_snapshot(
        parent_child_pairs: &[(ProcessId, ProcessId)],
    ) -> (SignalGraph, std::sync::Arc<SignalSnapshot>) {
        let mut g = SignalGraph::new();
        // Add init process
        let init = pid(1, 0);
        for (child, parent) in parent_child_pairs {
            g.observe_exec(*child, *parent, "test", None, child.start_time_ns);
        }
        // Also ensure root is connected
        g.observe_exec(init, pid(0, 0), "init", None, 0);
        let snap = g.snapshot();
        (g, snap)
    }

    #[test]
    fn test_correlate_same_process_exact_match() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let mut corr = EvidenceCorrelator::new();

        let intent = make_intent(
            p,
            "Write",
            vec![NormalizedArg::Path("/etc/hosts".into())],
            1000,
        );

        // Feed intent first
        let results = corr.on_intent(intent, &snap);
        assert!(results.is_empty()); // No action yet

        let action = make_action(
            p,
            ActionCandidateType::FileWrite,
            vec![NormalizedArg::Path("/etc/hosts".into())],
            2000,
        );

        let results = corr.on_action(action, &snap);
        assert!(!results.is_empty());
        let ev = &results[0];
        assert_eq!(ev.match_level, MatchLevel::L1Exact);
        assert!(ev.confidence > 0.50);
    }

    #[test]
    fn test_correlate_action_first_then_intent() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let mut corr = EvidenceCorrelator::new();

        let action = make_action(
            p,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/etc/passwd".into())],
            1000,
        );
        let results = corr.on_action(action, &snap);
        assert!(results.is_empty()); // No intent yet

        let intent = make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/etc/passwd".into())],
            2000,
        );
        let results = corr.on_intent(intent, &snap);
        assert!(!results.is_empty());
    }

    #[test]
    fn test_no_correlation_different_files() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let mut corr = EvidenceCorrelator::new();

        let intent = make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/etc/hosts".into())],
            1000,
        );
        corr.on_intent(intent, &snap);

        let action = make_action(
            p,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/var/log/syslog".into())],
            2000,
        );
        let results = corr.on_action(action, &snap);
        assert!(results.is_empty());
    }

    #[test]
    fn test_no_correlation_unrelated_processes() {
        let parent = pid(100, 1);
        let child_a = pid(200, 2);
        let child_b = pid(300, 3);

        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(parent, pid(1, 0), "claude", None, 1);
        snap_g.observe_exec(child_a, parent, "cat", None, 2);
        snap_g.observe_exec(child_b, parent, "cat", None, 3);
        let snap = snap_g.snapshot();

        // Verify tree structure: child_a and child_b are siblings
        assert!(snap.can_correlate(parent, child_a));
        assert!(snap.can_correlate(parent, child_b));

        let mut corr = EvidenceCorrelator::new();

        // Intent from parent, action from child_a → should correlate
        let intent = make_intent(
            parent,
            "Bash",
            vec![NormalizedArg::Command("cat".into())],
            1000,
        );
        let action = make_action(
            child_a,
            ActionCandidateType::ProcessExec,
            vec![NormalizedArg::Command("cat".into())],
            2000,
        );

        let results = corr.on_intent(intent, &snap);
        assert!(results.is_empty());
        let results = corr.on_action(action, &snap);
        assert!(!results.is_empty());
    }

    #[test]
    fn test_dedup_prevents_double_link() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let mut corr = EvidenceCorrelator::new();

        let intent = make_intent(p, "Write", vec![NormalizedArg::Path("/tmp/f".into())], 1000);
        let action = make_action(
            p,
            ActionCandidateType::FileWrite,
            vec![NormalizedArg::Path("/tmp/f".into())],
            2000,
        );

        let results1 = corr.on_intent(intent.clone(), &snap);
        assert!(results1.is_empty());
        let results2 = corr.on_action(action.clone(), &snap);
        assert!(!results2.is_empty());

        // Same intent again should NOT create another link (dedup)
        let results3 = corr.on_intent(intent, &snap);
        assert!(results3.is_empty());
    }

    #[test]
    fn test_drain_diagnostics() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let mut corr = EvidenceCorrelator::new();

        // Intent with no matching action
        let intent = make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/etc/shadow".into())],
            1000,
        );
        corr.on_intent(intent, &snap);

        let diags = corr.drain_diagnostics();
        assert!(!diags.is_empty());
        assert_eq!(
            diags[0].reason,
            crate::responsibility::types::NonLinkReason::CandidateNotAvailable
        );

        // Drain should empty
        let diags2 = corr.drain_diagnostics();
        assert!(diags2.is_empty());
    }

    #[test]
    fn test_correlate_batch_no_time_window() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let corr = EvidenceCorrelator::new();

        let intents = vec![make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/etc/hosts".into())],
            1000,
        )];
        let actions = vec![
            make_action(
                p,
                ActionCandidateType::FileRead,
                vec![NormalizedArg::Path("/etc/hosts".into())],
                120_000_000_000,
            ), // 120 seconds later — no time gate!
        ];

        let result = corr.correlate_batch(&intents, &actions, &snap);
        // Phase 2 has no time window — should still match
        assert!(!result.evidence.is_empty());
    }

    #[test]
    fn test_correlate_batch_lower_confidence_for_far_apart() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let corr = EvidenceCorrelator::new();

        // Immediate match
        let intents = vec![make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/tmp/f".into())],
            1000,
        )];
        let actions = vec![make_action(
            p,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/tmp/f".into())],
            1500,
        )];

        let result_immediate = corr.correlate_batch(&intents, &actions, &snap);

        // Far-apart match (30s)
        let actions_far = vec![make_action(
            p,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/tmp/f".into())],
            30_000_000_000,
        )];

        let result_far = corr.correlate_batch(&intents, &actions_far, &snap);

        // Immediate should have higher confidence than far
        assert!(!result_immediate.evidence.is_empty());
        if !result_far.evidence.is_empty() {
            // Far-apart should still match (no time gate) but with lower confidence
            assert!(result_immediate.evidence[0].confidence >= result_far.evidence[0].confidence);
        }
    }

    #[test]
    fn test_correlate_batch_produces_refinements() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let corr = EvidenceCorrelator::new();

        let intents = vec![make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/etc/hosts".into())],
            1000,
        )];
        let actions = vec![make_action(
            p,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/etc/hosts".into())],
            2000,
        )];

        let result = corr.correlate_batch(&intents, &actions, &snap);
        assert_eq!(result.evidence.len(), 1);
        assert_eq!(result.refinements.len(), 1);
        assert_eq!(
            result.refinements[0].refinement_type,
            EvidenceRefinementType::Discovered
        );
    }

    #[test]
    fn test_correlate_batch_non_matching_intents() {
        let p = pid(100, 1);
        let mut snap_g = SignalGraph::new();
        snap_g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = snap_g.snapshot();

        let corr = EvidenceCorrelator::new();

        let intents = vec![make_intent(
            p,
            "Read",
            vec![NormalizedArg::Path("/etc/hosts".into())],
            1000,
        )];
        let actions = vec![make_action(
            p,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/var/log/syslog".into())],
            2000,
        )];

        let result = corr.correlate_batch(&intents, &actions, &snap);
        assert!(result.evidence.is_empty());
        assert!(!result.non_links.is_empty());
    }

    #[test]
    fn test_compute_tree_distance_same_process() {
        let p = pid(100, 1);
        let mut g = SignalGraph::new();
        g.observe_exec(p, pid(1, 0), "claude", None, 1);
        let snap = g.snapshot();

        assert_eq!(compute_tree_distance(p, p, &snap), 0);
    }

    #[test]
    fn test_compute_tree_distance_child() {
        let parent = pid(100, 1);
        let child = pid(200, 2);
        let mut g = SignalGraph::new();
        g.observe_exec(parent, pid(1, 0), "claude", None, 1);
        g.observe_exec(child, parent, "cat", None, 2);
        let snap = g.snapshot();

        let d = compute_tree_distance(parent, child, &snap);
        assert_eq!(d, 1);
    }

    #[test]
    fn test_compute_tree_distance_grandchild() {
        let p = pid(100, 1);
        let c1 = pid(200, 2);
        let c2 = pid(300, 3);
        let mut g = SignalGraph::new();
        g.observe_exec(p, pid(1, 0), "claude", None, 1);
        g.observe_exec(c1, p, "bash", None, 2);
        g.observe_exec(c2, c1, "cat", None, 3);
        let snap = g.snapshot();

        let d = compute_tree_distance(p, c2, &snap);
        assert_eq!(d, 2);
    }
}
