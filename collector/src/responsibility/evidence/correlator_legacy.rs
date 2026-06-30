// SPDX-License-Identifier: MIT

use crate::responsibility::evidence::{
    ActionCandidate, ActionCandidateType, NonLinkDiagnostic, NonLinkReason,
};
use crate::responsibility::signals::SignalGraph;
use crate::responsibility::judgment::evaluate_accountability_policy;
use crate::responsibility::evidence::normalizer::{
    command_basename, normalize_path, normalize_url, tokenize_command,
};
use crate::responsibility::types::support_types::{CausalEdge, IntentEvent, ProcessId, ResourceId};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;
use std::path::Path;

const CORRELATION_EVIDENCE_SCHEMA_VERSION: u32 = 1;
const TIGHT_WINDOW_NS: u64 = 500_000_000; // 500ms
const WIDE_WINDOW_NS: u64 = 120_000_000_000; // 120s — LLM API round-trip can exceed 60s
const CONFIDENCE_CUTOFF: f32 = 0.50;

/// Validate that a causal edge has non-temporal evidence support.
///
/// Responsibility edges must never be created from temporal proximity alone.
/// Every edge must carry at least one evidence reference that traces back to
/// a concrete argument match, resource reference, or inference rule.
pub fn edge_has_non_temporal_support(edge: &CausalEdge) -> anyhow::Result<()> {
    if edge.evidence.is_empty() {
        anyhow::bail!("responsibility edge missing evidence");
    }
    Ok(())
}

/// Validate that a link candidate has non-temporal evidence before emission.
///
/// This is the runtime gate: every CorrelationEvidence must have matched_fields
/// (argument-match evidence) and a meaningful match level. Links formed
/// purely by temporal proximity are rejected here.
pub fn link_has_non_temporal_support(
    link: &CorrelationEvidence,
    match_level: &MatchLevel,
    dt_ns: u64,
) -> anyhow::Result<()> {
    if link.matched_fields.is_none() {
        anyhow::bail!("link missing matched_fields evidence");
    }
    // L5 Substring matches are the weakest; reject if they are the sole
    // evidence and the time delta exceeds the tight window.
    if *match_level == MatchLevel::L5Substring && dt_ns > TIGHT_WINDOW_NS {
        anyhow::bail!(
            "link relies on weak substring match outside tight window (dt={}ns)",
            dt_ns
        );
    }
    Ok(())
}

fn resource_from_candidate_for_policy(
    candidate_type: &ActionCandidateType,
    target: Option<&str>,
) -> ResourceId {
    match candidate_type {
        ActionCandidateType::FileRead
        | ActionCandidateType::FileWrite
        | ActionCandidateType::FileOverwrite => ResourceId::File {
            canonical_path: target.unwrap_or("(unknown path)").to_string(),
        },
        ActionCandidateType::NetworkConnect => {
            let target = target.unwrap_or("unknown:0");
            let (host, port) = target
                .rsplit_once(':')
                .and_then(|(host, port)| port.parse::<u16>().ok().map(|port| (host, port)))
                .unwrap_or((target, 0));
            ResourceId::Network {
                host: host.to_string(),
                port,
                scheme: None,
            }
        }
        ActionCandidateType::ProcessExec | ActionCandidateType::ProcessSpawn => {
            ResourceId::Process {
                process_id: ProcessId {
                    pid: 0,
                    start_time_ns: 0,
                },
            }
        }
    }
}

/// A causal link between an intent and an action candidate.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CorrelationEvidence {
    pub schema_version: u32,
    pub evidence_id: String,
    pub agent_id: String,
    pub intent_id: String,
    pub candidate_id: String,
    pub timestamp_ns: u64,
    pub confidence: f32,
    pub match_level: MatchLevel,
    pub matched_fields: Option<serde_json::Value>,
    pub supporting_raw_event_refs: Option<Vec<String>>,
    pub conversation_id: Option<String>,
    pub authority_source: Option<String>,
    pub message_id: Option<String>,
    pub source_event_ref: Option<String>,
    pub debug_notes: Option<String>,
    pub derivation_mode: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MatchLevel {
    L1Exact,
    L2Basename,
    L3Host,
    L4Argv0,
    L5Substring,
}

impl std::fmt::Display for MatchLevel {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::L1Exact => "l1_exact",
            Self::L2Basename => "l2_basename",
            Self::L3Host => "l3_host",
            Self::L4Argv0 => "l4_argv0",
            Self::L5Substring => "l5_substring",
        };
        write!(f, "{}", value)
    }
}

/// A deferred arbitration item emitted when the correlator cannot decide
/// between competing candidates. Resolved asynchronously by the analyzer layer
/// via the ObserverService.
#[derive(Debug, Clone)]
pub struct PendingArbitration {
    pub arbitration_id: String,
    pub agent_id: String,
    pub candidates: Vec<CorrelationEvidence>,
    /// "ambiguous_competition" or "below_confidence_cutoff"
    pub arbitration_type: String,
    pub timestamp_ns: u64,
}

/// Correlates intent events with action candidates using Argument Matching.
///
/// Priority: Argument Matching (L1-L5) → Temporal Proximity (tie-breaking only).
/// Temporal proximity NEVER creates a link on its own.
pub struct EvidenceCorrelator {
    intent_buffer: VecDeque<IntentEvent>,
    candidate_buffer: VecDeque<ActionCandidate>,
    non_link_diagnostics: Vec<NonLinkDiagnostic>,
    linked_intent_ids: HashSet<String>,
    linked_pairs: HashSet<(String, String)>,
    home_dir: Option<String>,
    env: HashMap<String, String>,
    pending_arbitrations: Vec<PendingArbitration>,
    observer_enabled: bool,
}

impl EvidenceCorrelator {
    pub fn new() -> Self {
        Self::with_observer(false)
    }

    pub fn with_observer(observer_enabled: bool) -> Self {
        let home_dir = std::env::var("HOME").ok();
        let env: HashMap<String, String> = std::env::vars().collect();
        Self {
            intent_buffer: VecDeque::new(),
            candidate_buffer: VecDeque::new(),
            non_link_diagnostics: Vec::new(),
            linked_intent_ids: HashSet::new(),
            linked_pairs: HashSet::new(),
            home_dir,
            env,
            pending_arbitrations: Vec::new(),
            observer_enabled,
        }
    }

    /// Process a new intent event; try to match against buffered candidates.
    pub fn on_intent(
        &mut self,
        intent: IntentEvent,
        graph: &SignalGraph,
    ) -> Vec<CorrelationEvidence> {
        self.evict_old(intent.timestamp_ns);

        if self.intent_buffer.iter().any(|existing| {
            existing.intent_id == intent.intent_id
                && existing.tool_name == intent.tool_name
                && existing.tool_args == intent.tool_args
                && existing.intent_class == intent.intent_class
                && existing.parse_state == intent.parse_state
                && existing.primary_matching_eligible == intent.primary_matching_eligible
                && existing.conversation_id == intent.conversation_id
        }) {
            return vec![];
        }

        if let Some(existing) = self.intent_buffer.iter_mut().find(|existing| {
            existing.intent_id == intent.intent_id
                && graph.snapshot().can_correlate(intent.process_id, existing.process_id)
                && existing.tool_name == intent.tool_name
                && existing.tool_args == intent.tool_args
                && existing.intent_class == intent.intent_class
                && existing.parse_state == intent.parse_state
                && existing.primary_matching_eligible == intent.primary_matching_eligible
        }) {
            *existing = intent;
            return vec![];
        }

        self.intent_buffer
            .retain(|existing| existing.intent_id != intent.intent_id);
        self.non_link_diagnostics.retain(|diagnostic| {
            diagnostic.intent_id.as_deref() != Some(intent.intent_id.as_str())
        });
        if !intent.primary_matching_eligible {
            self.record_non_link_diagnostic_for_ineligible_intent(&intent);
            self.intent_buffer.push_back(intent);
            return vec![];
        }

        let links = self.match_intent_to_candidates(&intent, graph);
        if !links.is_empty() {
            self.linked_intent_ids.insert(intent.intent_id.clone());
            for link in &links {
                self.linked_pairs
                    .insert((link.intent_id.clone(), link.candidate_id.clone()));
            }
        }
        if !self.linked_intent_ids.contains(&intent.intent_id) {
            self.record_non_link_diagnostics_for_intent(&intent, &links, graph);
        }
        self.intent_buffer.push_back(intent);
        links
    }

    /// Process a new action candidate; try to match against buffered intents.
    pub fn on_candidate(
        &mut self,
        candidate: ActionCandidate,
        graph: &SignalGraph,
    ) -> Vec<CorrelationEvidence> {
        self.evict_old(candidate.timestamp_ns);
        let links = self.match_candidate_to_intents(&candidate, graph);
        for link in &links {
            self.linked_intent_ids.insert(link.intent_id.clone());
            self.linked_pairs
                .insert((link.intent_id.clone(), link.candidate_id.clone()));
        }
        self.record_non_link_diagnostics_for_candidate(&candidate, &links, graph);
        self.candidate_buffer.push_back(candidate);
        links
    }

    pub fn take_non_link_diagnostics(&mut self) -> Vec<NonLinkDiagnostic> {
        std::mem::take(&mut self.non_link_diagnostics)
    }

    /// Returns the number of pending (unlinked) intents in the buffer.
    /// Used by integration tests to verify ordering guarantees.
    pub fn pending_intents(&self) -> usize {
        self.intent_buffer.len()
    }

    /// Returns the number of pending candidates in the buffer.
    /// Used by the analyzer flush protocol to detect drain completion.
    pub fn pending_candidates(&self) -> usize {
        self.candidate_buffer.len()
    }

    pub fn drain_pending_arbitrations(&mut self) -> Vec<PendingArbitration> {
        std::mem::take(&mut self.pending_arbitrations)
    }

    fn evict_old(&mut self, now_ns: u64) {
        while let Some(front) = self.intent_buffer.front() {
            if now_ns.saturating_sub(front.timestamp_ns) > WIDE_WINDOW_NS {
                if let Some(expired_intent) = self.intent_buffer.pop_front() {
                    if expired_intent.primary_matching_eligible
                        && !self.linked_intent_ids.contains(&expired_intent.intent_id)
                    {
                        self.non_link_diagnostics.push(
                            NonLinkDiagnostic::new(
                                now_ns,
                                Some(expired_intent.agent_id.clone()),
                                Some(expired_intent.intent_id.clone()),
                                None,
                                NonLinkReason::CandidateNotAvailable,
                                "candidate_filter".to_string(),
                            )
                            .with_intent_class(expired_intent.intent_class)
                            .with_parse_state(expired_intent.parse_state),
                        );
                    }
                    self.linked_intent_ids.remove(&expired_intent.intent_id);
                }
            } else {
                break;
            }
        }
        while let Some(front) = self.candidate_buffer.front() {
            if now_ns.saturating_sub(front.timestamp_ns) > WIDE_WINDOW_NS {
                self.candidate_buffer.pop_front();
            } else {
                break;
            }
        }
    }

    fn match_intent_to_candidates(
        &mut self,
        intent: &IntentEvent,
        graph: &SignalGraph,
    ) -> Vec<CorrelationEvidence> {
        if !intent.primary_matching_eligible {
            return vec![];
        }

        let candidates: Vec<_> = self
            .candidate_buffer
            .iter()
            .filter(|c| graph.snapshot().can_correlate(intent.process_id, c.process_id))
            .cloned()
            .collect();
        self.resolve_matches_for_intent(intent, candidates)
    }

    fn match_candidate_to_intents(
        &mut self,
        candidate: &ActionCandidate,
        graph: &SignalGraph,
    ) -> Vec<CorrelationEvidence> {
        let intents: Vec<_> = self
            .intent_buffer
            .iter()
            .filter(|i| {
                graph.snapshot().can_correlate(i.process_id, candidate.process_id)
            })
            .filter(|i| !self.linked_intent_ids.contains(&i.intent_id))
            .cloned()
            .collect();
        self.resolve_matches_for_candidate(candidate, intents)
    }

    fn resolve_matches_for_intent(
        &mut self,
        intent: &IntentEvent,
        candidates: Vec<ActionCandidate>,
    ) -> Vec<CorrelationEvidence> {
        let mut scored = Vec::new();
        for candidate in &candidates {
            if self
                .linked_pairs
                .contains(&(intent.intent_id.clone(), candidate.candidate_id.clone()))
            {
                continue;
            }
            if let Some(evidence) = self.build_link_candidate(intent, candidate) {
                scored.push(evidence);
            }
        }
        self.choose_links_for_intent(intent, scored)
    }

    fn resolve_matches_for_candidate(
        &mut self,
        candidate: &ActionCandidate,
        intents: Vec<IntentEvent>,
    ) -> Vec<CorrelationEvidence> {
        let mut scored = Vec::new();
        for intent in &intents {
            if self
                .linked_pairs
                .contains(&(intent.intent_id.clone(), candidate.candidate_id.clone()))
            {
                continue;
            }
            if let Some(evidence) = self.build_link_candidate(intent, candidate) {
                scored.push(evidence);
            }
        }
        self.choose_links_for_candidate(candidate, scored)
    }

    fn build_link_candidate(
        &self,
        intent: &IntentEvent,
        candidate: &ActionCandidate,
    ) -> Option<CorrelationEvidence> {
        let (level, mut matched_fields, mut confidence) = self.argument_match(intent, candidate)?;

        if let Some(object) = matched_fields.as_object_mut() {
            object.insert(
                "candidate_type".to_string(),
                serde_json::Value::String(candidate.candidate_type.to_string()),
            );
            let target = candidate
                .normalized_args
                .get("path")
                .and_then(|value| value.as_str())
                .map(|value| {
                    // Strip temp suffix for Edit tool so target matches intent file_path
                    strip_temp_suffix(value).unwrap_or_else(|| value.to_string())
                })
                .or_else(|| {
                    let host = candidate
                        .normalized_args
                        .get("host")
                        .and_then(|value| value.as_str())?;
                    let port = candidate
                        .normalized_args
                        .get("port")
                        .and_then(|value| value.as_u64())?;
                    Some(format!("{host}:{port}"))
                });
            if let Some(target_value) = target.clone() {
                object.insert(
                    "target".to_string(),
                    serde_json::Value::String(target_value),
                );
            }
            // Enrich matched_fields with action_args for alert pipeline
            let action_args = &candidate.normalized_args;
            if let Some(flags) = action_args.get("flags").and_then(|v| v.as_str()) {
                object.insert(
                    "flags".to_string(),
                    serde_json::Value::String(flags.to_string()),
                );
            }
            if let Some(host) = action_args.get("host").and_then(|v| v.as_str()) {
                object.insert(
                    "host".to_string(),
                    serde_json::Value::String(host.to_string()),
                );
            }
            if let Some(port) = action_args.get("port") {
                object.insert("port".to_string(), port.clone());
            }
            // Carry intent fields for alert pipeline (reasoning loop, etc.)
            object.insert(
                "tool_name".to_string(),
                serde_json::Value::String(intent.tool_name.clone()),
            );
            if let Some(tool_args_val) = serde_json::to_value(&intent.tool_args).ok() {
                if tool_args_val.is_object()
                    && !tool_args_val
                        .as_object()
                        .map(|o| o.is_empty())
                        .unwrap_or(true)
                {
                    object.insert("tool_args".to_string(), tool_args_val);
                }
            }
            let resource =
                resource_from_candidate_for_policy(&candidate.candidate_type, target.as_deref());
            let policy =
                evaluate_accountability_policy(&intent.agent_id, &resource, &[], &HashSet::new());
            if let Some(policy_id) = policy.policy_id.as_ref() {
                object.insert(
                    "policy_id".to_string(),
                    serde_json::Value::String(policy_id.0.clone()),
                );
            }
            if let Some(boundary_id) = policy.boundary_id.as_ref() {
                object.insert(
                    "boundary_id".to_string(),
                    serde_json::Value::String(boundary_id.0.clone()),
                );
            }
        }

        let dt = candidate.timestamp_ns.saturating_sub(intent.timestamp_ns);
        let mut debug_notes = None;
        if dt <= TIGHT_WINDOW_NS {
            confidence = (confidence + 0.03).min(0.95);
            debug_notes = Some("tight_time_window_boost".to_string());
        }

        // Enforce non-temporal evidence gate: reject links that would rely
        // solely on temporal proximity or weak substring evidence.
        let evidence = CorrelationEvidence {
            schema_version: CORRELATION_EVIDENCE_SCHEMA_VERSION,
            evidence_id: uuid::Uuid::new_v4().to_string(),
            agent_id: intent.agent_id.clone(),
            intent_id: intent.intent_id.clone(),
            candidate_id: candidate.candidate_id.clone(),
            timestamp_ns: candidate.timestamp_ns,
            confidence,
            match_level: level,
            matched_fields: Some(matched_fields),
            supporting_raw_event_refs: Some(candidate.raw_event_refs.clone()),
            conversation_id: intent.conversation_id.clone(),
            authority_source: Some(intent.authority_source.to_string()),
            message_id: intent.message_id.clone(),
            source_event_ref: intent.source_event_ref.clone(),
            debug_notes,
            derivation_mode: Some(candidate.derivation_mode.to_string()),
        };

        if let Err(e) = link_has_non_temporal_support(&evidence, &level, dt) {
            log::debug!(
                "Link rejected by non-temporal evidence gate: {} (agent={}, intent={}, candidate={}, match_level={:?})",
                e,
                intent.agent_id,
                intent.intent_id,
                candidate.candidate_id,
                level
            );
            return None;
        }

        Some(evidence)
    }

    fn choose_links_for_intent(
        &mut self,
        intent: &IntentEvent,
        mut candidates: Vec<CorrelationEvidence>,
    ) -> Vec<CorrelationEvidence> {
        if candidates.is_empty() {
            return vec![];
        }
        candidates.sort_by(|a, b| {
            b.confidence
                .partial_cmp(&a.confidence)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let best_confidence = candidates[0].confidence;
        let tied_best: Vec<_> = candidates
            .into_iter()
            .filter(|link| (link.confidence - best_confidence).abs() < f32::EPSILON)
            .collect();

        if best_confidence < CONFIDENCE_CUTOFF {
            if self.observer_enabled {
                let arbitration_id = uuid::Uuid::new_v4().to_string();
                let ts = tied_best.first().map(|l| l.timestamp_ns).unwrap_or(0);
                self.pending_arbitrations.push(PendingArbitration {
                    arbitration_id: arbitration_id.clone(),
                    agent_id: intent.agent_id.clone(),
                    candidates: tied_best.clone(),
                    arbitration_type: "below_confidence_cutoff".to_string(),
                    timestamp_ns: ts,
                });
                self.non_link_diagnostics.push(
                    NonLinkDiagnostic::new(
                        ts,
                        Some(intent.agent_id.clone()),
                        Some(intent.intent_id.clone()),
                        tied_best.first().map(|l| l.candidate_id.clone()),
                        NonLinkReason::ObserverPending,
                        "observer_arbitration".to_string(),
                    )
                    .with_intent_class(intent.intent_class)
                    .with_parse_state(intent.parse_state)
                    .with_confidence_observed(best_confidence)
                    .with_details(serde_json::json!({
                        "arbitration_id": arbitration_id,
                        "arbitration_type": "below_confidence_cutoff",
                        "candidate_ids": tied_best.iter().map(|l| l.candidate_id.clone()).collect::<Vec<_>>(),
                    })),
                );
                return vec![];
            }

            if let Some(best) = tied_best.first() {
                self.non_link_diagnostics.push(
                    NonLinkDiagnostic::new(
                        best.timestamp_ns,
                        Some(intent.agent_id.clone()),
                        Some(intent.intent_id.clone()),
                        Some(best.candidate_id.clone()),
                        NonLinkReason::BelowConfidenceCutoff,
                        "confidence_gate".to_string(),
                    )
                    .with_intent_class(intent.intent_class)
                    .with_parse_state(intent.parse_state)
                    .with_match_level_considered(best.match_level.to_string())
                    .with_confidence_observed(best.confidence),
                );
            }
            return vec![];
        }

        if tied_best.len() > 1 {
            let all_l1_exact_same_match = tied_best.iter().all(|link| {
                link.match_level == MatchLevel::L1Exact
                    && link.matched_fields == tied_best[0].matched_fields
            });

            if all_l1_exact_same_match {
                let latest_timestamp = tied_best
                    .iter()
                    .map(|link| link.timestamp_ns)
                    .max()
                    .unwrap_or(0);
                let latest_tied: Vec<_> = tied_best
                    .iter()
                    .filter(|link| link.timestamp_ns == latest_timestamp)
                    .cloned()
                    .collect();

                if latest_tied.len() == 1 {
                    return latest_tied;
                }
            }

            if self.observer_enabled && !all_l1_exact_same_match {
                let arbitration_id = uuid::Uuid::new_v4().to_string();
                let ts = tied_best[0].timestamp_ns;
                self.pending_arbitrations.push(PendingArbitration {
                    arbitration_id: arbitration_id.clone(),
                    agent_id: intent.agent_id.clone(),
                    candidates: tied_best.clone(),
                    arbitration_type: "ambiguous_competition".to_string(),
                    timestamp_ns: ts,
                });
                self.non_link_diagnostics.push(
                    NonLinkDiagnostic::new(
                        ts,
                        Some(intent.agent_id.clone()),
                        Some(intent.intent_id.clone()),
                        None,
                        NonLinkReason::ObserverPending,
                        "observer_arbitration".to_string(),
                    )
                    .with_intent_class(intent.intent_class)
                    .with_parse_state(intent.parse_state)
                    .with_confidence_observed(best_confidence)
                    .with_details(serde_json::json!({
                        "arbitration_id": arbitration_id,
                        "arbitration_type": "ambiguous_competition",
                        "candidate_ids": tied_best.iter().map(|link| link.candidate_id.clone()).collect::<Vec<_>>(),
                        "match_levels": tied_best.iter().map(|link| link.match_level.to_string()).collect::<Vec<_>>(),
                    })),
                );
                return vec![];
            }

            self.non_link_diagnostics.push(
                NonLinkDiagnostic::new(
                    tied_best[0].timestamp_ns,
                    Some(intent.agent_id.clone()),
                    Some(intent.intent_id.clone()),
                    None,
                    NonLinkReason::AmbiguousCompetition,
                    "confidence_gate".to_string(),
                )
                .with_intent_class(intent.intent_class)
                .with_parse_state(intent.parse_state)
                .with_confidence_observed(best_confidence)
                .with_details(serde_json::json!({
                    "candidate_ids": tied_best.iter().map(|link| link.candidate_id.clone()).collect::<Vec<_>>(),
                    "match_levels": tied_best.iter().map(|link| link.match_level.to_string()).collect::<Vec<_>>(),
                })),
            );
            return vec![];
        }

        vec![tied_best.into_iter().next().unwrap()]
    }

    fn choose_links_for_candidate(
        &mut self,
        candidate: &ActionCandidate,
        mut candidates: Vec<CorrelationEvidence>,
    ) -> Vec<CorrelationEvidence> {
        if candidates.is_empty() {
            return vec![];
        }
        candidates.sort_by(|a, b| {
            b.confidence
                .partial_cmp(&a.confidence)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let best_confidence = candidates[0].confidence;
        let tied_best: Vec<_> = candidates
            .into_iter()
            .filter(|link| (link.confidence - best_confidence).abs() < f32::EPSILON)
            .collect();

        if best_confidence < CONFIDENCE_CUTOFF {
            if self.observer_enabled {
                let arbitration_id = uuid::Uuid::new_v4().to_string();
                let ts = tied_best.first().map(|l| l.timestamp_ns).unwrap_or(0);
                let agent_id = tied_best
                    .first()
                    .map(|l| l.agent_id.clone())
                    .unwrap_or_default();
                self.pending_arbitrations.push(PendingArbitration {
                    arbitration_id: arbitration_id.clone(),
                    agent_id,
                    candidates: tied_best.clone(),
                    arbitration_type: "below_confidence_cutoff".to_string(),
                    timestamp_ns: ts,
                });
                self.non_link_diagnostics.push(
                    NonLinkDiagnostic::new(
                        ts,
                        tied_best.first().map(|l| l.agent_id.clone()),
                        tied_best.first().map(|l| l.intent_id.clone()),
                        Some(candidate.candidate_id.clone()),
                        NonLinkReason::ObserverPending,
                        "observer_arbitration".to_string(),
                    )
                    .with_confidence_observed(best_confidence)
                    .with_details(serde_json::json!({
                        "arbitration_id": arbitration_id,
                        "arbitration_type": "below_confidence_cutoff",
                    })),
                );
                return vec![];
            }

            if let Some(best) = tied_best.first() {
                self.non_link_diagnostics.push(
                    NonLinkDiagnostic::new(
                        best.timestamp_ns,
                        Some(best.agent_id.clone()),
                        Some(best.intent_id.clone()),
                        Some(candidate.candidate_id.clone()),
                        NonLinkReason::BelowConfidenceCutoff,
                        "confidence_gate".to_string(),
                    )
                    .with_match_level_considered(best.match_level.to_string())
                    .with_confidence_observed(best.confidence),
                );
            }
            return vec![];
        }

        if tied_best.len() > 1 {
            let all_same_match = tied_best.iter().all(|link| {
                link.match_level == tied_best[0].match_level
                    && link.matched_fields == tied_best[0].matched_fields
            });

            if all_same_match {
                let mut deterministic = tied_best;
                deterministic.sort_by(|a, b| {
                    b.timestamp_ns
                        .cmp(&a.timestamp_ns)
                        .then_with(|| a.source_event_ref.cmp(&b.source_event_ref))
                        .then_with(|| a.message_id.cmp(&b.message_id))
                        .then_with(|| a.intent_id.cmp(&b.intent_id))
                });
                return vec![deterministic.into_iter().next().unwrap()];
            }

            if self.observer_enabled {
                let arbitration_id = uuid::Uuid::new_v4().to_string();
                let ts = tied_best[0].timestamp_ns;
                let agent_id = tied_best[0].agent_id.clone();
                self.pending_arbitrations.push(PendingArbitration {
                    arbitration_id: arbitration_id.clone(),
                    agent_id,
                    candidates: tied_best.clone(),
                    arbitration_type: "ambiguous_competition".to_string(),
                    timestamp_ns: ts,
                });
                self.non_link_diagnostics.push(
                    NonLinkDiagnostic::new(
                        ts,
                        Some(tied_best[0].agent_id.clone()),
                        None,
                        Some(candidate.candidate_id.clone()),
                        NonLinkReason::ObserverPending,
                        "observer_arbitration".to_string(),
                    )
                    .with_confidence_observed(best_confidence)
                    .with_details(serde_json::json!({
                        "arbitration_id": arbitration_id,
                        "arbitration_type": "ambiguous_competition",
                        "intent_ids": tied_best.iter().map(|link| link.intent_id.clone()).collect::<Vec<_>>(),
                        "match_levels": tied_best.iter().map(|link| link.match_level.to_string()).collect::<Vec<_>>(),
                    })),
                );
                return vec![];
            }

            self.non_link_diagnostics.push(
                NonLinkDiagnostic::new(
                    tied_best[0].timestamp_ns,
                    Some(tied_best[0].agent_id.clone()),
                    None,
                    Some(candidate.candidate_id.clone()),
                    NonLinkReason::AmbiguousCompetition,
                    "confidence_gate".to_string(),
                )
                .with_confidence_observed(best_confidence)
                .with_details(serde_json::json!({
                    "intent_ids": tied_best.iter().map(|link| link.intent_id.clone()).collect::<Vec<_>>(),
                    "match_levels": tied_best.iter().map(|link| link.match_level.to_string()).collect::<Vec<_>>(),
                })),
            );
            return vec![];
        }

        vec![tied_best.into_iter().next().unwrap()]
    }

    fn record_non_link_diagnostics_for_candidate(
        &mut self,
        candidate: &ActionCandidate,
        links: &[CorrelationEvidence],
        graph: &SignalGraph,
    ) {
        for intent in self.intent_buffer.iter() {
            if !graph.snapshot().can_correlate(intent.process_id, candidate.process_id)
                || !intent.primary_matching_eligible
            {
                continue;
            }
            if self.linked_intent_ids.contains(&intent.intent_id) {
                continue;
            }

            let linked = links.iter().any(|link| {
                link.intent_id == intent.intent_id && link.candidate_id == candidate.candidate_id
            });
            if linked {
                continue;
            }

            let diagnostic = NonLinkDiagnostic::new(
                candidate.timestamp_ns.max(intent.timestamp_ns),
                Some(intent.agent_id.clone()),
                Some(intent.intent_id.clone()),
                Some(candidate.candidate_id.clone()),
                NonLinkReason::NoArgumentMatch,
                "argument_match".to_string(),
            )
            .with_intent_class(intent.intent_class)
            .with_parse_state(intent.parse_state)
            .with_details(serde_json::json!({
                "checked_candidate_id": candidate.candidate_id,
                "checked_candidate_type": candidate.candidate_type.to_string(),
                "intent_args": format!("{:?}", self.extract_intent_args(intent)),
                "candidate_args": format!("{:?}", self.extract_candidate_args(candidate)),
            }));
            self.non_link_diagnostics.push(diagnostic);
        }
    }

    fn record_non_link_diagnostic_for_ineligible_intent(&mut self, intent: &IntentEvent) {
        self.non_link_diagnostics.push(
            NonLinkDiagnostic::new(
                intent.timestamp_ns,
                Some(intent.agent_id.clone()),
                Some(intent.intent_id.clone()),
                None,
                NonLinkReason::IntentNotPrimaryEligible,
                "eligibility_gate".to_string(),
            )
            .with_intent_class(intent.intent_class)
            .with_parse_state(intent.parse_state),
        );
    }

    fn record_non_link_diagnostics_for_intent(
        &mut self,
        intent: &IntentEvent,
        links: &[CorrelationEvidence],
        graph: &SignalGraph,
    ) {
        for candidate in self.candidate_buffer.iter() {
            if !graph.snapshot().can_correlate(intent.process_id, candidate.process_id) {
                continue;
            }

            let linked = links.iter().any(|link| {
                link.intent_id == intent.intent_id && link.candidate_id == candidate.candidate_id
            });
            if linked {
                continue;
            }

            let diagnostic = NonLinkDiagnostic::new(
                candidate.timestamp_ns.max(intent.timestamp_ns),
                Some(intent.agent_id.clone()),
                Some(intent.intent_id.clone()),
                Some(candidate.candidate_id.clone()),
                NonLinkReason::NoArgumentMatch,
                "argument_match".to_string(),
            )
            .with_intent_class(intent.intent_class)
            .with_parse_state(intent.parse_state)
            .with_details(serde_json::json!({
                "checked_candidate_id": candidate.candidate_id,
                "checked_candidate_type": candidate.candidate_type.to_string(),
                "intent_args": format!("{:?}", self.extract_intent_args(intent)),
                "candidate_args": format!("{:?}", self.extract_candidate_args(candidate)),
            }));
            self.non_link_diagnostics.push(diagnostic);
        }
    }

    /// Returns (MatchLevel, matched_fields, confidence) if any argument matches, else None.
    fn argument_match(
        &self,
        intent: &IntentEvent,
        candidate: &ActionCandidate,
    ) -> Option<(MatchLevel, serde_json::Value, f32)> {
        let intent_args = self.extract_intent_args(intent);
        let candidate_args = self.extract_candidate_args(candidate);

        let mut best: Option<(MatchLevel, serde_json::Value, f32)> = None;

        for ia in &intent_args {
            for ca in &candidate_args {
                if let Some((level, matched_fields, conf)) = Self::compare_args(ia, ca) {
                    match &best {
                        None => best = Some((level, matched_fields, conf)),
                        Some((_, _, bc)) if conf > *bc => {
                            best = Some((level, matched_fields, conf))
                        }
                        _ => {}
                    }
                }
            }
        }
        best
    }

    fn extract_intent_args(&self, intent: &IntentEvent) -> Vec<NormalizedArg> {
        let mut args = Vec::new();
        let input = &intent.tool_args;

        if let Some(path) = input.get("file_path").and_then(|v| v.as_str()) {
            let norm = normalize_path(path, self.home_dir.as_deref(), &self.env);
            args.push(NormalizedArg::Path(norm.to_string_lossy().to_string()));
        }
        if let Some(cmd) = input.get("command").and_then(|v| v.as_str()) {
            args.push(NormalizedArg::CommandFull(cmd.to_string()));

            let tokens = tokenize_command(cmd);
            // Extract command basenames from all sub-commands in shell pipelines.
            // Shell operators like &&, |, ; split pipelines; the token after each
            // operator is a sub-command whose basename should be extracted.
            let shell_ops: &[&str] = &[
                "&&", "||", "|", ";", ">", "<", ">>", "<<", ">|", "&>", ">&", "2>", "1>", "2>>",
                "&>>", "2>&1", "1>&2",
            ];
            let mut seen_commands: HashSet<String> = HashSet::new();
            for (i, token) in tokens.iter().enumerate() {
                if token.contains("://") {
                    args.push(NormalizedArg::Url(normalize_url(token)));
                    continue;
                }
                if token == "/dev/null" {
                    continue;
                }
                if token.starts_with('/') || token.starts_with("~/") {
                    let norm = normalize_path(token, self.home_dir.as_deref(), &self.env);
                    args.push(NormalizedArg::Path(norm.to_string_lossy().to_string()));
                    continue;
                }
                // Extract basename from a token if it looks like a command:
                // - first token, or follows a shell operator
                // - not an option (starts with -)
                // - not a shell operator itself
                if shell_ops.contains(&token.as_str()) {
                    continue;
                }
                if token.starts_with('-') {
                    continue;
                }
                let is_first = i == 0;
                let follows_op = i > 0 && shell_ops.contains(&tokens[i - 1].as_str());
                if is_first || follows_op {
                    let basename = std::path::Path::new(token)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or(token);
                    if !seen_commands.contains(basename) {
                        seen_commands.insert(basename.to_string());
                        args.push(NormalizedArg::Command(basename.to_string()));
                    }
                } else {
                    // Catch-all: any non-option, non-operator token that wasn't
                    // captured as a command is likely a positional argument (e.g.
                    // "tmp" in "ls tmp"). Treat it as a potential relative path.
                    let norm = normalize_path(token, self.home_dir.as_deref(), &self.env);
                    args.push(NormalizedArg::Path(norm.to_string_lossy().to_string()));
                }
            }
        }
        if let Some(url) = input.get("url").and_then(|v| v.as_str()) {
            args.push(NormalizedArg::Url(normalize_url(url)));
        }

        if let Some(extracted) = input.get("extracted_args").and_then(|v| v.as_array()) {
            for item in extracted {
                match item.get("type").and_then(|t| t.as_str()) {
                    Some("path") => {
                        if let Some(v) = item.get("value").and_then(|v| v.as_str()) {
                            let norm = normalize_path(v, self.home_dir.as_deref(), &self.env);
                            args.push(NormalizedArg::Path(norm.to_string_lossy().to_string()));
                        }
                    }
                    Some("url") => {
                        if let Some(v) = item.get("value").and_then(|v| v.as_str()) {
                            args.push(NormalizedArg::Url(normalize_url(v)));
                        }
                    }
                    Some("command") => {
                        if let Some(v) = item.get("value").and_then(|v| v.as_str()) {
                            args.push(NormalizedArg::Command(command_basename(v)));
                        }
                    }
                    _ => {}
                }
            }
        }
        args
    }

    fn extract_candidate_args(&self, candidate: &ActionCandidate) -> Vec<NormalizedArg> {
        let mut args = Vec::new();
        let a = &candidate.normalized_args;

        if let Some(path) = a.get("path")
            .or_else(|| a.get("file_path"))
            .and_then(|v| v.as_str())
        {
            let norm = normalize_path(path, self.home_dir.as_deref(), &self.env);
            let norm_str = norm.to_string_lossy().to_string();
            args.push(NormalizedArg::Path(norm_str.clone()));
            // Detect Claude Code Edit tool temp file pattern:
            //   {target}.tmp.{pid}.{timestamp_ns}
            // and emit the stripped target path so file_write candidates
            // can match the intent's file_path for race conflict detection.
            if let Some(stripped) = strip_temp_suffix(&norm_str) {
                args.push(NormalizedArg::Path(stripped));
            }
        }
        if let Some(argv) = a.get("argv").and_then(|v| v.as_array()) {
            if let Some(first) = argv.first().and_then(|v| v.as_str()) {
                args.push(NormalizedArg::Command(command_basename(first)));
            }
        }
        if let Some(host) = a.get("host").and_then(|v| v.as_str()) {
            let host = host.to_lowercase();
            if let Some(port) = a.get("port").and_then(|v| v.as_u64()) {
                args.push(NormalizedArg::Url(format!("{host}:{port}")));
            }
            args.push(NormalizedArg::Url(host));
        }
        args
    }

    fn compare_args(
        ia: &NormalizedArg,
        aa: &NormalizedArg,
    ) -> Option<(MatchLevel, serde_json::Value, f32)> {
        match (ia, aa) {
            (NormalizedArg::Path(ip), NormalizedArg::Path(ap)) => {
                if ip == ap {
                    Some((
                        MatchLevel::L1Exact,
                        serde_json::json!({"intent_arg": ip, "candidate_arg": ap}),
                        0.90,
                    ))
                } else if !ip.is_empty() && !ap.is_empty() {
                    let ib = Path::new(ip).file_name()?.to_str()?;
                    let ab = Path::new(ap).file_name()?.to_str()?;
                    if ib == ab {
                        Some((
                            MatchLevel::L2Basename,
                            serde_json::json!({
                                "intent_arg": ip,
                                "candidate_arg": ap,
                                "intent_basename": ib,
                                "candidate_basename": ab,
                            }),
                            0.70,
                        ))
                    } else if ap.contains(ip.as_str()) && ip.len() >= 8 {
                        Some((
                            MatchLevel::L5Substring,
                            serde_json::json!({"intent_arg": ip, "candidate_arg": ap}),
                            0.40,
                        ))
                    } else {
                        None
                    }
                } else {
                    None
                }
            }
            (NormalizedArg::Url(iu), NormalizedArg::Url(au)) => {
                if iu == au {
                    Some((
                        MatchLevel::L1Exact,
                        serde_json::json!({"intent_arg": iu, "candidate_arg": au}),
                        0.90,
                    ))
                } else {
                    let ih = iu.split('/').nth(2).unwrap_or(iu);
                    let ah = au.split('/').nth(2).unwrap_or(au);
                    if !ih.is_empty() && ih == ah {
                        Some((
                            MatchLevel::L3Host,
                            serde_json::json!({
                                "intent_arg": iu,
                                "candidate_arg": au,
                                "intent_host": ih,
                                "candidate_host": ah,
                            }),
                            0.75,
                        ))
                    } else {
                        None
                    }
                }
            }
            (NormalizedArg::Command(ic), NormalizedArg::Command(ac)) => {
                if ic == ac {
                    Some((
                        MatchLevel::L4Argv0,
                        serde_json::json!({"intent_arg": ic, "candidate_arg": ac}),
                        0.65,
                    ))
                } else {
                    None
                }
            }
            (NormalizedArg::CommandFull(ic), NormalizedArg::Command(ac)) => {
                if ic.contains(ac.as_str()) && ac.len() >= 3 {
                    Some((
                        MatchLevel::L5Substring,
                        serde_json::json!({"intent_arg": ic, "candidate_arg": ac}),
                        0.40,
                    ))
                } else {
                    None
                }
            }
            _ => None,
        }
    }
}

/// Strip Claude Code Edit tool temp file suffix: `{target}.tmp.{pid}.{timestamp}` → `{target}`
fn strip_temp_suffix(path: &str) -> Option<String> {
    // Look for pattern: ".tmp." followed by digits, ".", digits at the end
    let tmp_marker = path.rfind(".tmp.")?;
    let after_tmp = &path[tmp_marker + 5..]; // skip ".tmp."
    // After ".tmp." should be: digits.digits
    let dot_pos = after_tmp.find('.')?;
    let pid_part = &after_tmp[..dot_pos];
    let ts_part = &after_tmp[dot_pos + 1..];
    if pid_part.chars().all(|c| c.is_ascii_digit())
        && ts_part.chars().all(|c| c.is_ascii_digit())
        && pid_part.len() >= 3
        && ts_part.len() >= 10
    {
        Some(path[..tmp_marker].to_string())
    } else {
        None
    }
}

#[derive(Debug)]
enum NormalizedArg {
    Path(String),
    Url(String),
    Command(String),
    CommandFull(String),
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::types::support_types::ProcessId;
    use crate::responsibility::{
        ActionCandidateType, CandidateDerivationMode, Lossiness, NonLinkReason, SignalClass,
    };
    use serde_json::Value;

    /// Test helper: create a default empty signal graph.
    fn g() -> SignalGraph {
        SignalGraph::new()
    }

    /// Test extension trait for correlator calls with an empty graph.
    trait CorrelatorTestExt {
        fn on_intent_t(&mut self, intent: IntentEvent) -> Vec<CorrelationEvidence>;
        fn on_candidate_t(&mut self, candidate: ActionCandidate) -> Vec<CorrelationEvidence>;
    }

    impl CorrelatorTestExt for EvidenceCorrelator {
        fn on_intent_t(&mut self, intent: IntentEvent) -> Vec<CorrelationEvidence> {
            let graph = g();
            self.on_intent(intent, &graph)
        }
        fn on_candidate_t(&mut self, candidate: ActionCandidate) -> Vec<CorrelationEvidence> {
            let graph = g();
            self.on_candidate(candidate, &graph)
        }
    }

    fn make_intent(tool: &str, args: Value, ts: u64) -> IntentEvent {
        let mut intent = IntentEvent::new(
            uuid::Uuid::new_v4().to_string(),
            "agent-1".to_string(),
            ts,
            tool.to_string(),
            args,
        );
        // Use same pid as make_candidate so graph-less can_correlate works in tests.
        intent.process_id = ProcessId::new(100, 500);
        intent
    }

    fn make_candidate(syscall: &str, args: Value, ts: u64) -> ActionCandidate {
        let mut action = crate::responsibility::types::support_types::ActionEvent::new(
            "action-1".to_string(),
            ProcessId::new(100, 500),
            None,
            ts,
            syscall.to_string(),
            args,
            "bash".to_string(),
        );
        action.assign_agent("agent-1".to_string());
        crate::responsibility::ActionCandidate::from_action_event(&action).unwrap()
    }

    #[test]
    fn test_causal_link_includes_appendix_explainability_fields() {
        let mut c = EvidenceCorrelator::new();
        let mut intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/shadow"}),
            1000,
        );
        intent.set_conversation_id("conversation-1".to_string());
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/etc/shadow", "flags": "O_RDONLY"}),
            1200,
        );

        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);

        assert_eq!(links.len(), 1);
        let link = &links[0];
        assert_eq!(link.schema_version, 1);
        assert_eq!(
            link.supporting_raw_event_refs.as_deref(),
            Some(&["action-1".to_string()][..])
        );
        assert_eq!(link.conversation_id.as_deref(), Some("conversation-1"));
        assert_eq!(link.debug_notes.as_deref(), Some("tight_time_window_boost"));
        assert_eq!(
            link.matched_fields
                .as_ref()
                .and_then(|v| v.get("intent_arg"))
                .and_then(|v| v.as_str()),
            Some("/etc/shadow")
        );
        assert_eq!(
            link.matched_fields
                .as_ref()
                .and_then(|v| v.get("candidate_arg"))
                .and_then(|v| v.as_str()),
            Some("/etc/shadow")
        );
    }

    #[test]
    fn test_l1_exact_path_match() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/shadow"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/etc/shadow", "flags": "O_RDONLY"}),
            1200,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].match_level, MatchLevel::L1Exact);
        assert!(links[0].confidence >= 0.90);
    }

    #[test]
    fn test_l4_command_match() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent("Bash", serde_json::json!({"command": "npm test"}), 1000);
        let candidate =
            make_candidate("execve", serde_json::json!({"argv": ["npm", "test"]}), 1200);
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].match_level, MatchLevel::L4Argv0);
    }

    #[test]
    fn test_bash_command_with_absolute_script_path_matches_exec_candidate_path() {
        let mut c = EvidenceCorrelator::new();
        let script = "/home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh";
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": format!("{} --verbose", script)}),
            1000,
        );
        let candidate = make_candidate(
            "execve",
            serde_json::json!({"argv": [script, "--verbose"], "path": script}),
            1200,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].match_level, MatchLevel::L1Exact);
    }

    #[test]
    fn test_bash_command_with_env_prefix_matches_exec_candidate_path() {
        let mut c = EvidenceCorrelator::new();
        let script = "/home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh";
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": format!("RESUME_TOKEN=1 {} --verbose --force", script)}),
            1000,
        );
        let candidate = make_candidate(
            "execve",
            serde_json::json!({"argv": [script, "--verbose", "--force"], "path": script}),
            1200,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].match_level, MatchLevel::L1Exact);
    }

    #[test]
    fn test_no_match_different_paths() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/passwd"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/foo.txt", "flags": "O_RDONLY"}),
            1200,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert!(links.is_empty());
    }

    #[test]
    fn test_temporal_only_no_link() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/passwd"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/var/log/syslog", "flags": "O_RDONLY"}),
            1100,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert!(
            links.is_empty(),
            "Temporal proximity alone must not create a link"
        );
    }

    #[test]
    fn test_wide_window_eviction() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/shadow"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/etc/shadow", "flags": "O_RDONLY"}),
            121_000_000_001,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert!(
            links.is_empty(),
            "Events outside 120s window should not match"
        );
    }

    #[test]
    fn test_partial_intent_not_used_for_primary_matching() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent("Read", serde_json::json!({}), 1000)
            .with_parse_state(crate::responsibility::IntentParseState::Malformed);
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/etc/shadow", "flags": "O_RDONLY"}),
            1200,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert!(
            links.is_empty(),
            "Malformed intents must not enter primary matching"
        );
    }

    #[test]
    fn test_orchestration_intent_not_used_for_primary_matching() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "TaskCreate",
            serde_json::json!({"description": "delegate"}),
            1000,
        );
        let candidate = make_candidate(
            "execve",
            serde_json::json!({"argv": ["bash", "-lc", "ls"]}),
            1200,
        );
        c.on_intent_t(intent);
        let links = c.on_candidate_t(candidate);
        assert!(
            links.is_empty(),
            "Orchestration intents must stay out of primary matching"
        );
    }

    #[test]
    fn test_unmatched_primary_eligible_intent_emits_no_argument_match_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/passwd"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/other.txt", "flags": "O_RDONLY"}),
            1200,
        );

        let expected_candidate_id = candidate.candidate_id.clone();
        let intent_result = c.on_intent_t(intent.clone());
        assert!(intent_result.is_empty());
        c.on_candidate_t(candidate);

        let diagnostics = c.take_non_link_diagnostics();
        assert_eq!(diagnostics.len(), 1);
        let diagnostic = &diagnostics[0];
        assert_eq!(
            diagnostic.intent_id.as_deref(),
            Some(intent.intent_id.as_str())
        );
        assert_eq!(
            diagnostic.candidate_id.as_deref(),
            Some(expected_candidate_id.as_str())
        );
        assert_eq!(diagnostic.reason, NonLinkReason::NoArgumentMatch);
        assert_eq!(diagnostic.stage, "argument_match");
    }

    #[test]
    fn test_non_primary_eligible_intent_emits_intent_not_primary_eligible_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent("Read", serde_json::json!({}), 1000)
            .with_parse_state(crate::responsibility::IntentParseState::Malformed);

        let links = c.on_intent_t(intent.clone());
        assert!(links.is_empty());

        let diagnostics = c.take_non_link_diagnostics();
        assert_eq!(diagnostics.len(), 1);
        let diagnostic = &diagnostics[0];
        assert_eq!(
            diagnostic.intent_id.as_deref(),
            Some(intent.intent_id.as_str())
        );
        assert_eq!(diagnostic.reason, NonLinkReason::IntentNotPrimaryEligible);
        assert_eq!(diagnostic.stage, "eligibility_gate");
        assert_eq!(diagnostic.intent_class, Some(intent.intent_class.to_string()));
        assert_eq!(diagnostic.parse_state, Some(intent.parse_state.to_string()));
    }

    #[test]
    fn test_candidate_first_no_match_emits_no_argument_match_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/already-buffered.txt", "flags": "O_RDONLY"}),
            1000,
        );
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/passwd"}),
            1200,
        );
        let expected_candidate_id = candidate.candidate_id.clone();

        let candidate_links = c.on_candidate_t(candidate);
        assert!(candidate_links.is_empty());
        let intent_links = c.on_intent_t(intent.clone());
        assert!(intent_links.is_empty());

        let diagnostics = c.take_non_link_diagnostics();
        assert_eq!(diagnostics.len(), 1);
        let diagnostic = &diagnostics[0];
        assert_eq!(
            diagnostic.intent_id.as_deref(),
            Some(intent.intent_id.as_str())
        );
        assert_eq!(
            diagnostic.candidate_id.as_deref(),
            Some(expected_candidate_id.as_str())
        );
        assert_eq!(diagnostic.reason, NonLinkReason::NoArgumentMatch);
        assert_eq!(diagnostic.stage, "argument_match");
    }

    #[test]
    fn test_expired_primary_intent_emits_candidate_not_available_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/etc/passwd"}),
            1000,
        );
        let mut later_intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/later.txt"}),
            121_000_000_001,
        );
        later_intent.agent_id = "agent-2".to_string();

        let first_links = c.on_intent_t(intent.clone());
        assert!(first_links.is_empty());
        let second_links = c.on_intent_t(later_intent);
        assert!(second_links.is_empty());

        let diagnostics = c.take_non_link_diagnostics();
        assert!(diagnostics.iter().any(|diagnostic| {
            diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                && diagnostic.reason == NonLinkReason::CandidateNotAvailable
                && diagnostic.stage == "candidate_filter"
        }));
    }

    #[test]
    fn test_expired_linked_intent_does_not_emit_candidate_not_available_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/linked.txt"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/linked.txt", "flags": "O_RDONLY"}),
            1200,
        );
        let mut later_intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/later.txt"}),
            121_000_000_001,
        );
        later_intent.agent_id = "agent-2".to_string();

        c.on_candidate_t(candidate);
        let links = c.on_intent_t(intent.clone());
        assert_eq!(links.len(), 1);

        c.on_intent_t(later_intent);

        let diagnostics = c.take_non_link_diagnostics();
        assert!(!diagnostics.iter().any(|diagnostic| {
            diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                && diagnostic.reason == NonLinkReason::CandidateNotAvailable
                && diagnostic.stage == "candidate_filter"
        }));
    }

    #[test]
    fn test_expired_intent_linked_via_on_candidate_does_not_emit_candidate_not_available_diagnostic()
     {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/linked-late.txt"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/linked-late.txt", "flags": "O_RDONLY"}),
            1200,
        );
        let mut later_intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/later.txt"}),
            121_000_000_001,
        );
        later_intent.agent_id = "agent-2".to_string();

        let initial_links = c.on_intent_t(intent.clone());
        assert!(initial_links.is_empty());
        let links = c.on_candidate_t(candidate);
        assert_eq!(links.len(), 1);

        c.on_intent_t(later_intent);

        let diagnostics = c.take_non_link_diagnostics();
        assert!(!diagnostics.iter().any(|diagnostic| {
            diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                && diagnostic.reason == NonLinkReason::CandidateNotAvailable
                && diagnostic.stage == "candidate_filter"
        }));
    }

    #[test]
    fn test_linked_intent_does_not_emit_no_argument_match_for_later_unrelated_candidates() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/linked.txt"}),
            1000,
        );
        let matching_candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/linked.txt", "flags": "O_RDONLY"}),
            1200,
        );
        let unrelated_candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/unrelated.txt", "flags": "O_RDONLY"}),
            1500,
        );

        let initial_links = c.on_intent_t(intent.clone());
        assert!(initial_links.is_empty());
        let links = c.on_candidate_t(matching_candidate);
        assert_eq!(links.len(), 1);

        let later_links = c.on_candidate_t(unrelated_candidate);
        assert!(later_links.is_empty());

        let diagnostics = c.take_non_link_diagnostics();
        assert!(
            !diagnostics.iter().any(|diagnostic| {
                diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                    && diagnostic.reason == NonLinkReason::NoArgumentMatch
                    && diagnostic.stage == "argument_match"
            }),
            "linked intents should not keep accumulating no_argument_match noise for later unrelated candidates"
        );
    }

    #[test]
    fn test_bash_intent_prefers_closest_exact_candidate_over_stale_exact_match() {
        let mut c = EvidenceCorrelator::new();
        let script = "/home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh";
        let candidate_one = make_candidate(
            "execve",
            serde_json::json!({"argv": [script, "--verbose"], "path": script}),
            1_000,
        );
        let candidate_two = make_candidate(
            "execve",
            serde_json::json!({"argv": [script, "--verbose", "--force"], "path": script}),
            2_000,
        );
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": format!("{} --verbose --force", script)}),
            3_000,
        );

        c.on_candidate_t(candidate_one);
        c.on_candidate_t(candidate_two.clone());
        let links = c.on_intent_t(intent.clone());

        assert_eq!(links.len(), 1);
        assert_eq!(links[0].candidate_id, candidate_two.candidate_id);

        let diagnostics = c.take_non_link_diagnostics();
        assert!(!diagnostics.iter().any(|diagnostic| {
            diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                && diagnostic.reason == NonLinkReason::AmbiguousCompetition
                && diagnostic.stage == "confidence_gate"
        }));
    }

    #[test]
    fn test_same_intent_candidate_pair_is_emitted_only_once() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": "/bin/bash /home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh"}),
            1000,
        );
        let candidate = make_candidate(
            "execve",
            serde_json::json!({
                "argv": ["/bin/bash", "/home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh"],
                "path": "/bin/bash"
            }),
            1200,
        );

        let first_links = c.on_intent_t(intent.clone());
        assert!(first_links.is_empty());

        let second_links = c.on_candidate_t(candidate.clone());
        assert_eq!(second_links.len(), 1);

        let third_links = c.on_intent_t(intent.clone());
        assert!(
            third_links.is_empty(),
            "same intent should not emit duplicate link for an already-linked candidate"
        );
    }

    #[test]
    fn test_linked_intent_does_not_expand_to_later_candidates() {
        let mut c = EvidenceCorrelator::new();
        let script = "/home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh";
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": format!("bash {}", script)}),
            1_000,
        );
        let first_candidate = make_candidate(
            "execve",
            serde_json::json!({"argv": ["/bin/bash"], "path": "/bin/bash"}),
            1_200,
        );
        let second_candidate = make_candidate(
            "openat",
            serde_json::json!({"path": script, "flags": "O_RDONLY"}),
            2_000,
        );

        let initial_links = c.on_intent_t(intent.clone());
        assert!(initial_links.is_empty());

        let first_links = c.on_candidate_t(first_candidate);
        assert_eq!(first_links.len(), 1);
        assert_eq!(first_links[0].intent_id, intent.intent_id);

        let later_links = c.on_candidate_t(second_candidate);
        assert!(
            later_links.is_empty(),
            "an intent that already formed a primary link should not keep expanding to later candidates"
        );
    }

    #[test]
    fn test_intent_metadata_upgrade_does_not_reemit_existing_link() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": "/bin/bash /home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh"}),
            1_000,
        );
        let candidate = make_candidate(
            "execve",
            serde_json::json!({
                "argv": ["/bin/bash", "/home/lzk/mtagentlens/env/scenarios/fixtures/reasoning-loop/broken_script.sh"],
                "path": "/bin/bash"
            }),
            1_200,
        );

        let first_links = c.on_intent_t(intent.clone());
        assert!(first_links.is_empty());

        let linked = c.on_candidate_t(candidate.clone());
        assert_eq!(linked.len(), 1);

        let mut upgraded_intent = intent.clone();
        upgraded_intent.set_conversation_id("resp_123".to_string());
        let replayed = c.on_intent_t(upgraded_intent);
        assert!(
            replayed.is_empty(),
            "metadata-only upgrades must not clear prior link state and re-emit the same pair"
        );
    }

    #[test]
    fn test_intent_with_primary_link_does_not_emit_no_argument_match_for_other_buffered_candidates()
    {
        let mut c = EvidenceCorrelator::new();
        let matching_candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/linked.txt", "flags": "O_RDONLY"}),
            1_000,
        );
        let unrelated_candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/unrelated.txt", "flags": "O_RDONLY"}),
            1_100,
        );
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/linked.txt"}),
            1_200,
        );

        c.on_candidate_t(matching_candidate);
        c.on_candidate_t(unrelated_candidate);
        let links = c.on_intent_t(intent.clone());
        assert_eq!(links.len(), 1);

        let diagnostics = c.take_non_link_diagnostics();
        assert!(
            !diagnostics.iter().any(|diagnostic| {
                diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                    && diagnostic.reason == NonLinkReason::NoArgumentMatch
                    && diagnostic.stage == "argument_match"
            }),
            "once an intent forms a primary link, it should not also accumulate no_argument_match noise for other buffered candidates in the same correlation pass"
        );
    }

    #[test]
    fn test_ambiguous_competition_suppresses_tied_links_and_emits_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/shared.txt"}),
            1000,
        );
        let candidate_one = make_candidate(
            "openat",
            serde_json::json!({"path": "/var/tmp/shared.txt", "flags": "O_RDONLY"}),
            1200,
        );
        let candidate_two = make_candidate(
            "openat",
            serde_json::json!({"path": "/opt/tmp/shared.txt", "flags": "O_RDONLY"}),
            1300,
        );

        c.on_candidate_t(candidate_one.clone());
        c.on_candidate_t(candidate_two.clone());
        let links = c.on_intent_t(intent.clone());

        assert!(links.is_empty());
        let diagnostics = c.take_non_link_diagnostics();
        assert!(diagnostics.iter().any(|diagnostic| {
            diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                && diagnostic.reason == NonLinkReason::AmbiguousCompetition
                && diagnostic.stage == "confidence_gate"
        }));
    }

    #[test]
    fn test_same_ambiguous_competition_is_not_reemitted_for_intent_upgrade() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/tmp/shared.txt"}),
            2_000,
        );
        let candidate_one = make_candidate(
            "openat",
            serde_json::json!({"path": "/var/tmp/shared.txt", "flags": "O_RDONLY"}),
            1_000,
        );
        let candidate_two = make_candidate(
            "openat",
            serde_json::json!({"path": "/opt/tmp/shared.txt", "flags": "O_RDONLY"}),
            1_100,
        );

        c.on_candidate_t(candidate_one);
        c.on_candidate_t(candidate_two);

        let first_links = c.on_intent_t(intent.clone());
        assert!(first_links.is_empty());
        let first_diagnostics = c.take_non_link_diagnostics();
        let first_ambiguous: Vec<_> = first_diagnostics
            .iter()
            .filter(|diagnostic| {
                diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                    && diagnostic.reason == NonLinkReason::AmbiguousCompetition
            })
            .collect();
        assert_eq!(first_ambiguous.len(), 1);

        let mut upgraded_intent = intent.clone();
        upgraded_intent.set_conversation_id("resp_123".to_string());
        let replayed_links = c.on_intent_t(upgraded_intent);
        assert!(replayed_links.is_empty());
        let second_diagnostics = c.take_non_link_diagnostics();
        let second_ambiguous: Vec<_> = second_diagnostics
            .iter()
            .filter(|diagnostic| {
                diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                    && diagnostic.reason == NonLinkReason::AmbiguousCompetition
            })
            .collect();
        assert!(
            second_ambiguous.is_empty(),
            "the same unresolved ambiguity should not be re-emitted when only intent metadata changes"
        );
    }

    #[test]
    fn test_repeated_identical_bash_intents_do_not_ambiguously_compete_for_one_network_candidate() {
        let mut c = EvidenceCorrelator::new();
        let command = "curl -v --max-time 5 -o /dev/null http://203.0.113.10:9000/beacon";

        let mut intent_one = make_intent("Bash", serde_json::json!({"command": command}), 1_000);
        intent_one.set_message_id("toolu_call_one".to_string());
        let mut intent_two = make_intent("Bash", serde_json::json!({"command": command}), 1_000);
        intent_two.set_message_id("toolu_call_two".to_string());
        let mut intent_three = make_intent("Bash", serde_json::json!({"command": command}), 1_000);
        intent_three.set_message_id("toolu_call_three".to_string());

        let candidate = ActionCandidate::new(
            "candidate-network-1".to_string(),
            1_200,
            ActionCandidateType::NetworkConnect,
            ProcessId::new(100, 500),
            serde_json::json!({"host": "203.0.113.10", "port": 9000}),
            vec!["action-1".to_string()],
            CandidateDerivationMode::Direct,
            Lossiness::Lossless,
            Some("agent-1".to_string()),
            None,
            None,
            Some(SignalClass::HighSignal),
            None,
        )
        .unwrap();

        c.on_intent_t(intent_one.clone());
        c.on_intent_t(intent_two.clone());
        c.on_intent_t(intent_three.clone());
        let links = c.on_candidate_t(candidate);

        assert_eq!(
            links.len(),
            1,
            "identical repeated Bash intents should deterministically claim one network candidate"
        );
        assert_eq!(links[0].match_level, MatchLevel::L3Host);

        let diagnostics = c.take_non_link_diagnostics();
        assert!(
            !diagnostics.iter().any(|diagnostic| {
                diagnostic.reason == NonLinkReason::AmbiguousCompetition
                    && diagnostic.candidate_id.as_deref() == Some("candidate-network-1")
            }),
            "repeated identical Bash intents should not emit ambiguous_competition for the same network candidate"
        );
    }

    #[test]
    fn test_bash_curl_url_prefers_network_candidate_over_dev_null_sink() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Bash",
            serde_json::json!({"command": "curl -v --max-time 5 -o /dev/null http://203.0.113.10:9000/beacon"}),
            1_000,
        );
        let dev_null_candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/dev/null", "flags": "O_WRONLY|O_CREAT|O_TRUNC"}),
            1_100,
        );
        let network_candidate = ActionCandidate::new(
            "candidate-network-dev-null".to_string(),
            1_200,
            ActionCandidateType::NetworkConnect,
            ProcessId::new(100, 500),
            serde_json::json!({"host": "203.0.113.10", "port": 9000}),
            vec!["action-network".to_string()],
            CandidateDerivationMode::Direct,
            Lossiness::Lossless,
            Some("agent-1".to_string()),
            None,
            None,
            Some(SignalClass::HighSignal),
            None,
        )
        .unwrap();

        c.on_candidate_t(dev_null_candidate);
        c.on_candidate_t(network_candidate.clone());
        let links = c.on_intent_t(intent);

        assert_eq!(
            links.len(),
            1,
            "curl intent should form exactly one primary link"
        );
        assert_eq!(links[0].candidate_id, network_candidate.candidate_id);
        assert_eq!(links[0].match_level, MatchLevel::L3Host);
    }

    #[test]
    fn test_below_confidence_match_emits_below_confidence_cutoff_diagnostic() {
        let mut c = EvidenceCorrelator::new();
        let intent = make_intent(
            "Read",
            serde_json::json!({"file_path": "/workspace/very-sensitive-target.txt"}),
            1000,
        );
        let candidate = make_candidate(
            "openat",
            serde_json::json!({"path": "/tmp/cache/workspace/very-sensitive-target.txt.backup", "flags": "O_RDONLY"}),
            1200,
        );

        c.on_candidate_t(candidate.clone());
        let links = c.on_intent_t(intent.clone());

        assert!(links.is_empty());
        let diagnostics = c.take_non_link_diagnostics();
        assert!(diagnostics.iter().any(|diagnostic| {
            diagnostic.intent_id.as_deref() == Some(intent.intent_id.as_str())
                && diagnostic.candidate_id.as_deref() == Some(candidate.candidate_id.as_str())
                && diagnostic.reason.to_string() == "below_confidence_cutoff"
                && diagnostic.stage == "confidence_gate"
        }));
    }

    #[test]
    fn test_non_temporal_gate_rejects_weak_substring_outside_tight_window() {
        let evidence = CorrelationEvidence {
            schema_version: CORRELATION_EVIDENCE_SCHEMA_VERSION,
            evidence_id: "test-link-1".to_string(),
            agent_id: "agent-1".to_string(),
            intent_id: "intent-1".to_string(),
            candidate_id: "candidate-1".to_string(),
            timestamp_ns: 2000,
            confidence: 0.45,
            match_level: MatchLevel::L5Substring,
            matched_fields: Some(serde_json::json!({"path": "/tmp/x"})),
            supporting_raw_event_refs: Some(vec!["evt-1".to_string()]),
            conversation_id: None,
            authority_source: None,
            message_id: None,
            source_event_ref: None,
            debug_notes: None,
            derivation_mode: None,
        };
        // dt = 1000ns > TIGHT_WINDOW_NS (500ms), L5 match
        let result =
            link_has_non_temporal_support(&evidence, &MatchLevel::L5Substring, 1_000_000_000);
        assert!(
            result.is_err(),
            "weak substring outside tight window must be rejected"
        );
    }

    #[test]
    fn test_non_temporal_gate_accepts_strong_match() {
        let evidence = CorrelationEvidence {
            schema_version: CORRELATION_EVIDENCE_SCHEMA_VERSION,
            evidence_id: "test-link-2".to_string(),
            agent_id: "agent-1".to_string(),
            intent_id: "intent-2".to_string(),
            candidate_id: "candidate-2".to_string(),
            timestamp_ns: 2000,
            confidence: 0.92,
            match_level: MatchLevel::L1Exact,
            matched_fields: Some(serde_json::json!({"path": "/etc/shadow"})),
            supporting_raw_event_refs: Some(vec!["evt-2".to_string()]),
            conversation_id: None,
            authority_source: None,
            message_id: None,
            source_event_ref: None,
            debug_notes: None,
            derivation_mode: None,
        };
        let result = link_has_non_temporal_support(&evidence, &MatchLevel::L1Exact, 1_000_000_000);
        assert!(result.is_ok(), "strong L1 match must pass the gate");
    }

    #[test]
    fn test_edge_has_non_temporal_support_rejects_empty_evidence() {
        use crate::responsibility::types::support_types::{
            CausalEdge, CausalityType, EdgeId, EvidenceId, EvidenceRef, NodeId,
        };
        let edge = CausalEdge {
            edge_id: EdgeId("edge-1".to_string()),
            from_node_id: NodeId("node-a".to_string()),
            to_node_id: NodeId("node-b".to_string()),
            causality_type: CausalityType::Direct,
            confidence: 0.7,
            evidence: vec![],
        };
        assert!(edge_has_non_temporal_support(&edge).is_err());

        let edge_with_evidence = CausalEdge {
            edge_id: EdgeId("edge-2".to_string()),
            from_node_id: NodeId("node-c".to_string()),
            to_node_id: NodeId("node-d".to_string()),
            causality_type: CausalityType::Direct,
            confidence: 0.9,
            evidence: vec![EvidenceRef {
                evidence_id: EvidenceId("evidence-1".to_string()),
            }],
        };
        assert!(edge_has_non_temporal_support(&edge_with_evidence).is_ok());
    }
}
