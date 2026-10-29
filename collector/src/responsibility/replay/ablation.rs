// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// replay/ablation.rs — raw-fact staircase reconstruction.
//
// This module rebuilds the L0/L0.5/L1/L2/L3 ablation layers from the facts that
// existed before the authoritative resolver ran. It deliberately does not read
// `AnalysisCheckpoint::graph` or `AnalysisCheckpoint::identities`: the graph is
// rebuilt from the frozen evidence links, intents and actions, and identity is
// resolved from the PRE-resolver input under a documented condition.
//
// Every layer uses the same frozen scoring kernel, gate and thresholds, and
// `hard_sensitive_egress` is forced off. L0 reports a fixed raw-activity index
// (raw OS action occurrences) and no kernel breakdown; the other layers report
// the kernel score. Every layer exports its per-run `ranking_score` for
// score-based metrics. Observer observations can change the verdict without
// changing the continuous score, so both are reported separately.

use crate::responsibility::classification::candidates::{
    CandidateSummary, MAX_REVIEW_CANDIDATES, bind_task_context, enrich_candidate_outcomes,
    graph_candidates_with, review_observations, unreviewed_candidates_of,
};
use crate::responsibility::classification::engine::{
    ClassificationEngine, normalize_observer_output,
};
use crate::responsibility::classification::final_decision::{
    FinalVerdict, Observation, aggregate_final_decision,
};
use crate::responsibility::classification::rule_engine::RuleEngine;
use crate::responsibility::classification::rules::RuleDecision;
use crate::responsibility::classification::scoring::ScoreSignals;
use crate::responsibility::classification::scoring::{ScoringConfig, compute_score_breakdown_with};
use crate::responsibility::graph::{
    GraphBuilder, GraphConfig, GraphPatternMatcher, ResponsibilityGraph, default_templates,
};
use crate::responsibility::identity::{
    IdentityBoundaryPolicy, IdentityCondition, IdentityProvider, IdentityView, IdentityViewError,
    RawIdentityInput,
};
use crate::responsibility::judgment::DelegationObservation;
use crate::responsibility::observer::provider::ObserverProvider;
use crate::responsibility::observer::types::ObserverDecision;
use crate::responsibility::replay::checkpoint::{
    AnalysisCheckpoint, FrozenGraphInputs, identity_units_from_facts, quality_status,
};
use crate::responsibility::types::{
    Action, AgentId, CorrelationEvidence, Intent, IntentId, MessageId, NormalizedArg, ProcessId,
    ResponsibilityEdgeType, ScoreBreakdown,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fmt;

pub const ABLATION_SCHEMA: &str = "mtagentlens.ablation-rebuild/v1";
pub const ABLATION_PROTOCOL: &str = "raw-fact-staircase-v1";
pub const ABLATION_REVIEW_CACHE_SCHEMA: &str = "mtagentlens.ablation-review-cache/1";

/// Live review batch for ablation. The production cap is 64 candidates, but a
/// 64-candidate JSON verdict at max_tokens=4096 can truncate; 16 keeps every
/// batch parseable while preserving one verdict per presented candidate.
const ABLATION_REVIEW_BATCH: usize = MAX_REVIEW_CANDIDATES;
pub const SCORING_KERNEL: &str = "v9-six-dimension-fusion+platt+v2-gate";
/// `score_kind` for a numeric, non-binary ranking score. AUROC consumes the
/// ordering, not a calibrated probability.
pub const CONTINUOUS_SCORE_KIND: &str = "continuous";
/// Exact definition of the L0 per-run ranking score.
pub const L0_ACTIVITY_SCORE_DEFINITION: &str = "raw-activity-index:raw-os-action-occurrences";

/// The five fixed ablation layers. The spelling is part of the exported JSON.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AblationLayer {
    #[serde(rename = "L0")]
    L0,
    #[serde(rename = "L0.5")]
    L0p5,
    #[serde(rename = "L1")]
    L1,
    #[serde(rename = "L2")]
    L2,
    #[serde(rename = "L3")]
    L3,
}

impl AblationLayer {
    pub const ALL: [Self; 5] = [Self::L0, Self::L0p5, Self::L1, Self::L2, Self::L3];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::L0 => "L0",
            Self::L0p5 => "L0.5",
            Self::L1 => "L1",
            Self::L2 => "L2",
            Self::L3 => "L3",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AblationState {
    /// Every input this layer needs is present.
    Available,
    /// The layer is computable, but a documented input is missing.
    Partial,
    /// The layer cannot be computed without inventing an input.
    Unavailable,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AblationError {
    UnavailableIdentityInput,
    UnavailableIdentityPolicy,
    IdentityView(IdentityViewError),
    Serialization(String),
    InvalidRawFacts(String),
    InvalidReviewCache(String),
    ReviewUnavailable(String),
}

impl fmt::Display for AblationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnavailableIdentityInput => write!(
                f,
                "ablation unavailable: checkpoint has no frozen PRE-resolver identity input"
            ),
            Self::UnavailableIdentityPolicy => write!(
                f,
                "ablation unavailable: checkpoint has no frozen identity boundary policy"
            ),
            Self::IdentityView(err) => write!(f, "ablation unavailable: {err}"),
            Self::Serialization(err) => write!(f, "ablation serialization error: {err}"),
            Self::InvalidRawFacts(err) => write!(f, "ablation invalid raw facts: {err}"),
            Self::InvalidReviewCache(err) => write!(f, "ablation invalid review cache: {err}"),
            Self::ReviewUnavailable(err) => write!(f, "ablation review unavailable: {err}"),
        }
    }
}

impl std::error::Error for AblationError {}

impl From<IdentityViewError> for AblationError {
    fn from(err: IdentityViewError) -> Self {
        Self::IdentityView(err)
    }
}

/// The only checkpoint fields an ablation layer may see.
///
/// This struct is the compile-time boundary: it has no `graph` and no
/// `identities` field, so no layer can reach the frozen final answer by
/// accident. `from_checkpoint` copies the raw slices only.
#[derive(Debug, Clone, Copy)]
pub struct RawFactInput<'a> {
    pub identity_input: &'a RawIdentityInput,
    pub identity_policy: &'a IdentityBoundaryPolicy,
    pub intents: &'a [Intent],
    pub actions: &'a [Action],
    pub evidence: &'a [CorrelationEvidence],
    pub observations: &'a [Observation],
    pub delegations: &'a [DelegationObservation],
    pub score_signals: &'a ScoreSignals,
    pub classification_config: &'a crate::responsibility::observer::types::ClassificationConfig,
    pub workspace_prefix: &'a str,
    pub disable_patterns: bool,
    pub observer_enabled: Option<bool>,
    pub capture_quality: &'a serde_json::Value,
    /// Optional PRE-resolver graph context. `None` means a historical
    /// checkpoint and keeps the graph layers partial; `Some(empty)` is a
    /// validated complete snapshot.
    pub graph_inputs: Option<&'a FrozenGraphInputs>,
}

impl<'a> RawFactInput<'a> {
    /// Copy only PRE-resolver facts out of a checkpoint.
    ///
    /// Historical checkpoints without the frozen identity input or policy are
    /// rejected; the layers that need identity are never scored from a guess.
    pub fn from_checkpoint(checkpoint: &'a AnalysisCheckpoint) -> Result<Self, AblationError> {
        let identity_input = checkpoint
            .identity_input
            .as_ref()
            .ok_or(AblationError::UnavailableIdentityInput)?;
        let identity_policy = checkpoint
            .identity_policy
            .as_ref()
            .ok_or(AblationError::UnavailableIdentityPolicy)?;
        Ok(Self {
            identity_input,
            identity_policy,
            intents: &checkpoint.intents,
            actions: &checkpoint.actions,
            evidence: &checkpoint.evidence,
            observations: &checkpoint.observations,
            delegations: &checkpoint.delegations,
            score_signals: &checkpoint.score_signals,
            classification_config: &checkpoint.classification_config,
            workspace_prefix: &checkpoint.workspace_prefix,
            disable_patterns: checkpoint.disable_patterns,
            observer_enabled: checkpoint.observer_enabled,
            capture_quality: &checkpoint.capture_quality,
            graph_inputs: checkpoint.graph_inputs.as_ref(),
        })
    }
}

/// What a layer may see.
///
/// `observations` and `observer_enabled` are zeroed for L0/L0.5/L1: those
/// layers ablate the Observer signal, so exposing its count/state would leak
/// the variable under test even if the verdict itself stayed `None`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VisibleFacts {
    pub intents: usize,
    pub actions: usize,
    pub evidence: usize,
    pub observations: usize,
    pub processes: usize,
    pub identity_units: usize,
    pub identity_assigned: usize,
    pub identity_unknown: usize,
    pub observer_enabled: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GraphFacts {
    pub nodes: usize,
    pub edges: usize,
    pub intent_action_edges: usize,
    pub pbc_edges: usize,
    pub evidenced_pbc_edges: usize,
    pub taint_edges: usize,
    pub context_edges: usize,
    pub delegation_edges: usize,
    pub competing_edges: usize,
    pub pattern_matches: usize,
    pub safe_rules: usize,
    pub unsafe_rules: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScoreReport {
    pub kernel: String,
    pub system_score: f32,
    pub raw_score: f32,
    pub calibrated_score: f32,
    pub gate_factor: f32,
    pub gate_version: String,
    pub rule_score: f32,
    pub pattern_score: f32,
    pub boundary_score: f32,
    pub evidence_score: f32,
    pub role_score: f32,
    pub repetition_score: f32,
    pub behavioral_score: f32,
    pub threshold_unsafe: f32,
    pub threshold_safe: f32,
    pub hard_sensitive_egress: bool,
    pub scoring: ScoringConfig,
}

impl
    From<(
        ScoreBreakdown,
        &crate::responsibility::observer::types::ClassificationConfig,
    )> for ScoreReport
{
    fn from(
        (breakdown, config): (
            ScoreBreakdown,
            &crate::responsibility::observer::types::ClassificationConfig,
        ),
    ) -> Self {
        Self {
            kernel: SCORING_KERNEL.into(),
            system_score: breakdown.total,
            raw_score: breakdown.raw_score,
            calibrated_score: breakdown.calibrated_score,
            gate_factor: breakdown.gate_factor,
            gate_version: breakdown.gate_version.clone(),
            rule_score: breakdown.rule_score,
            pattern_score: breakdown.pattern_score,
            boundary_score: breakdown.boundary_score,
            evidence_score: breakdown.evidence_score,
            role_score: breakdown.role_score,
            repetition_score: breakdown.repetition_score,
            behavioral_score: breakdown.behavioral_score,
            threshold_unsafe: config.threshold_unsafe,
            threshold_safe: config.threshold_safe,
            hard_sensitive_egress: config.hard_sensitive_egress,
            scoring: config.scoring,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AblationLayerReport {
    pub layer: AblationLayer,
    pub state: AblationState,
    pub unavailable_reason: Option<String>,
    pub visible: VisibleFacts,
    pub identity_condition: Option<IdentityCondition>,
    pub graph: Option<GraphFacts>,
    pub score: Option<ScoreReport>,
    pub score_route: Option<String>,
    /// Continuous per-run ranking score for score-based endpoints
    /// (AUROC/TPR/FPR). L0 carries the frozen raw-activity index; L0.5-L3
    /// carry the frozen six-dimension `system_score`. `None` only when the
    /// layer cannot be computed. This is a ranking value, never a calibrated
    /// detection probability.
    #[serde(default)]
    pub ranking_score: Option<f64>,
    /// Numeric kind of `ranking_score`. `continuous` means the value is
    /// ordinal/continuous and suitable for AUROC, not a binary verdict label.
    #[serde(default)]
    pub score_kind: Option<String>,
    /// Exact definition of `ranking_score`, so an activity index cannot be
    /// mistaken for the six-dimension kernel or a probability.
    #[serde(default)]
    pub score_definition: Option<String>,
    /// Observer/catalog verdict. `None` means the layer is not a verdict layer,
    /// not that the run was safe.
    pub verdict: Option<FinalVerdict>,
    /// Full enriched and task-context-bound candidates this layer would present
    /// to the Observer. Empty for layers that do not review candidates.
    #[serde(default = "empty_review_candidates")]
    pub review_candidates: serde_json::Value,
    /// Exact bounded task view whose fingerprint binds the review candidates.
    #[serde(default)]
    pub review_task_context: Option<serde_json::Value>,
    pub limitations: Vec<String>,
}

fn empty_review_candidates() -> serde_json::Value {
    serde_json::Value::Array(Vec::new())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AblationReport {
    pub schema: String,
    pub protocol: String,
    pub run_id: String,
    pub source_fingerprint: String,
    /// SHA-256 over the raw facts listed in `RawFactInput` only.
    pub raw_fact_fingerprint: String,
    /// Both booleans are asserted false so a consumer can check the boundary.
    pub read_checkpoint_graph: bool,
    pub read_checkpoint_identities: bool,
    pub capture_status: String,
    pub observer_enabled: Option<bool>,
    #[serde(default)]
    pub review_cache_provenance: Option<String>,
    #[serde(default)]
    pub review_cache_sha256: Option<String>,
    pub layers: Vec<AblationLayerReport>,
    pub limitations: Vec<String>,
}

/// One layer's offline Observer/ScopedCandidate material.
/// Explicit provenance of a review cache. Hand-written caches are never
/// reported as provider output.
fn external_unverified() -> String {
    "external_unverified".into()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AblationReviewLayer {
    pub layer: AblationLayer,
    pub observations: Vec<Observation>,
    /// Present only when produced by the live provider path. Unknown token and
    /// cost values stay `null`, never inferred.
    #[serde(default)]
    pub metadata: Option<AblationReviewMetadata>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct AblationReviewMetadata {
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub input_sha256: String,
    #[serde(default)]
    pub response_sha256: String,
    #[serde(default)]
    pub call_count: u32,
    #[serde(default)]
    pub token_usage: Option<u64>,
    #[serde(default)]
    pub cost_usd: Option<f64>,
}

/// Strict typed offline review cache for L2/L3.
///
/// It carries only review material, never a production verdict. The run,
/// implementation and raw-fact fingerprint bind it to one exact reconstruction.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AblationReviewCache {
    pub schema: String,
    pub run_id: String,
    pub source_implementation: String,
    pub raw_fact_fingerprint: String,
    #[serde(default = "external_unverified")]
    pub provenance: String,
    pub layers: Vec<AblationReviewLayer>,
}

impl AblationReviewCache {
    fn validate(
        &self,
        checkpoint: &AnalysisCheckpoint,
        raw_fact_fingerprint: &str,
    ) -> Result<(), AblationError> {
        if self.schema != ABLATION_REVIEW_CACHE_SCHEMA {
            return Err(AblationError::InvalidReviewCache(format!(
                "unexpected schema {}",
                self.schema
            )));
        }
        if self.run_id != checkpoint.run_id {
            return Err(AblationError::InvalidReviewCache(format!(
                "run {} does not match checkpoint run {}",
                self.run_id, checkpoint.run_id
            )));
        }
        if self.source_implementation != checkpoint.implementation {
            return Err(AblationError::InvalidReviewCache(
                "source implementation does not match the checkpoint".into(),
            ));
        }
        if self.raw_fact_fingerprint != raw_fact_fingerprint {
            return Err(AblationError::InvalidReviewCache(
                "raw-fact fingerprint does not match the reconstruction".into(),
            ));
        }
        let mut seen = HashSet::new();
        for layer in &self.layers {
            if !matches!(layer.layer, AblationLayer::L2 | AblationLayer::L3) {
                return Err(AblationError::InvalidReviewCache(format!(
                    "layer {} cannot carry reviews",
                    layer.layer.as_str()
                )));
            }
            if !seen.insert(layer.layer) {
                return Err(AblationError::InvalidReviewCache(format!(
                    "duplicate layer {}",
                    layer.layer.as_str()
                )));
            }
            if layer.observations.is_empty() {
                return Err(AblationError::InvalidReviewCache(format!(
                    "layer {} has no review observations",
                    layer.layer.as_str()
                )));
            }
            for observation in &layer.observations {
                if let Observation::ScopedReview { confidence, .. } = observation
                    && (!confidence.is_finite() || !(0.0..=1.0).contains(confidence))
                {
                    return Err(AblationError::InvalidReviewCache(
                        "invalid review confidence".into(),
                    ));
                }
                match observation {
                    Observation::ScopedReview { candidates, .. } if !candidates.is_empty() => {}
                    Observation::ScopedCandidate { .. } => {}
                    Observation::ObserverSafe { .. }
                    | Observation::ObserverUncertain { .. }
                    | Observation::ObserverFailed { .. } => {}
                    Observation::ScopedReview { .. } => {
                        return Err(AblationError::InvalidReviewCache(format!(
                            "layer {} has an empty scoped review",
                            layer.layer.as_str()
                        )));
                    }
                    _ => {
                        return Err(AblationError::InvalidReviewCache(format!(
                            "layer {} only accepts scoped review material",
                            layer.layer.as_str()
                        )));
                    }
                }
            }
        }
        Ok(())
    }
}

/// Rebuild the staircase from a checkpoint's PRE-resolver facts.
pub fn rebuild(checkpoint: &AnalysisCheckpoint) -> Result<AblationReport, AblationError> {
    let raw = RawFactInput::from_checkpoint(checkpoint)?;
    let mut report = rebuild_from_raw_facts(&raw)?;
    report.run_id = checkpoint.run_id.clone();
    report.source_fingerprint = checkpoint.implementation.clone();
    Ok(report)
}

/// Rebuild with strict per-layer offline review material.
///
/// The cache is validated against the raw-fact fingerprint before any override
/// is applied. A layer without a cache entry keeps its original observations
/// (including an unmatched-cache Partial state).
pub fn rebuild_with_reviews(
    checkpoint: &AnalysisCheckpoint,
    cache: &AblationReviewCache,
) -> Result<AblationReport, AblationError> {
    let raw = RawFactInput::from_checkpoint(checkpoint)?;
    let fingerprint = raw_fact_fingerprint(&raw)?;
    cache.validate(checkpoint, &fingerprint)?;
    // Origin metadata is declared by a cache file, not authenticated attestation.
    let mut overrides: HashMap<AblationLayer, Vec<Observation>> = HashMap::new();
    for layer in &cache.layers {
        overrides.insert(layer.layer, layer.observations.clone());
    }
    let mut report = rebuild_from_raw_facts_with_overrides(&raw, &overrides)?;
    debug_assert_eq!(report.raw_fact_fingerprint, fingerprint);
    report.run_id = checkpoint.run_id.clone();
    report.source_fingerprint = checkpoint.implementation.clone();
    report.review_cache_provenance = Some(cache.provenance.clone());
    report.review_cache_sha256 = Some(sha256_json(cache)?);
    report.limitations.push("Cache provenance is caller-declared; hashes bind bytes, not an authenticated model execution.".into());
    Ok(report)
}

/// Produce a typed review cache through the configured Observer provider.
///
/// This is the only path allowed to mark a cache as provider-evaluated. It
/// rebuilds the exact deterministic L2/L3 inputs and uses the same shared
/// candidate/task/citation normalization as runtime review.
pub async fn review_with_provider(
    checkpoint: &AnalysisCheckpoint,
    provider: &dyn ObserverProvider,
) -> Result<AblationReviewCache, AblationError> {
    let raw = RawFactInput::from_checkpoint(checkpoint)?;
    validate_raw_facts(&raw)?;
    let graph_inputs = checkpoint
        .graph_inputs
        .as_ref()
        .ok_or_else(|| AblationError::ReviewUnavailable("frozen graph inputs are absent".into()))?;
    graph_inputs
        .validate(raw.intents)
        .map_err(AblationError::ReviewUnavailable)?;
    if quality_status(raw.capture_quality) != "healthy" {
        return Err(AblationError::ReviewUnavailable(
            "capture quality is not healthy".into(),
        ));
    }
    if raw.observer_enabled != Some(true) {
        return Err(AblationError::ReviewUnavailable(
            "Observer is disabled or its state is unknown".into(),
        ));
    }

    let units = identity_units_from_facts(raw.identity_input, raw.intents, raw.actions)?;
    let pid_view = IdentityView::resolve(
        raw.identity_input,
        &units,
        IdentityCondition::PidInstanceOnly,
        raw.identity_policy,
    )?;
    let full_view = IdentityView::resolve(
        raw.identity_input,
        &units,
        IdentityCondition::Full,
        raw.identity_policy,
    )?;
    let pid_graph = rebuild_graph(&raw, &pid_view, true)?;
    let full_graph = rebuild_graph(&raw, &full_view, true)?;
    let engine = ClassificationEngine::with_workspace_and_config(
        raw.workspace_prefix.to_string(),
        frozen_config(&raw),
    );
    let descriptions = review_intent_descriptions(raw.intents);
    let observed_task = observed_task_context(&raw);
    let fingerprint = raw_fact_fingerprint(&raw)?;

    let mut layers = Vec::new();
    for (layer, graph, include_rule_catalog) in [
        (AblationLayer::L2, &pid_graph, false),
        (AblationLayer::L3, &full_graph, true),
    ] {
        let patterns = patterns_for(&raw, graph);
        let candidates = current_candidates(&raw, graph, &patterns);
        if candidates.is_empty() {
            continue;
        }
        let mut observations = Vec::new();
        let mut input_hashes = Vec::new();
        let mut response_hashes = Vec::new();
        for presented in candidates.chunks(ABLATION_REVIEW_BATCH) {
            let input = engine.build_observer_review_input(
                graph,
                &patterns,
                raw.evidence,
                presented,
                &descriptions,
                Some(&observed_task),
                include_rule_catalog,
            );
            let input_sha256 = sha256_json(&input)?;
            let mut output = provider.evaluate(&input).await.map_err(|error| {
                AblationError::ReviewUnavailable(format!("provider call failed: {error:?}"))
            })?;
            if !output.confidence.is_finite() || !(0.0..=1.0).contains(&output.confidence) {
                return Err(AblationError::ReviewUnavailable(format!(
                    "provider returned unsupported confidence {}",
                    output.confidence
                )));
            }
            if output.decision == ObserverDecision::Unsafe
                && !output.evidence_cited.iter().any(|citation| {
                    presented.iter().any(|candidate| {
                        citation == &candidate.candidate.id
                            || candidate.evidence_ids.contains(citation)
                    })
                })
            {
                return Err(AblationError::ReviewUnavailable(
                    "provider returned an unscoped Unsafe result".into(),
                ));
            }
            normalize_observer_output(presented, &mut output);
            let response_sha256 = sha256_json(&output)?;
            let batch_observations = review_observations(
                presented,
                review_alert_decision(output.decision),
                output.confidence,
                &output.evidence_cited,
            );
            if !batch_observations
                .iter()
                .all(|observation| match observation {
                    Observation::ScopedReview { candidates, .. } => !candidates.is_empty(),
                    Observation::ScopedCandidate { .. } => true,
                    // A review with no security-critical cited candidate is a
                    // legitimate whole-run safe/uncertain/failed verdict.
                    Observation::ObserverSafe { .. }
                    | Observation::ObserverUncertain { .. }
                    | Observation::ObserverFailed { .. } => true,
                    _ => false,
                })
            {
                return Err(AblationError::ReviewUnavailable(
                    "provider result did not bind to the presented candidates".into(),
                ));
            }
            input_hashes.push(input_sha256);
            response_hashes.push(response_sha256);
            observations.extend(batch_observations);
        }
        // Commit to ordered request/response digests for all bounded batches.
        let input_sha256 = sha256_json(&input_hashes)?;
        let response_sha256 = sha256_json(&response_hashes)?;
        let call_count = input_hashes.len() as u32;
        layers.push(AblationReviewLayer {
            layer,
            observations,
            metadata: Some(AblationReviewMetadata {
                mode: "provider_evaluation".into(),
                input_sha256,
                response_sha256,
                call_count,
                token_usage: None,
                cost_usd: None,
            }),
        });
    }

    Ok(AblationReviewCache {
        schema: ABLATION_REVIEW_CACHE_SCHEMA.into(),
        run_id: checkpoint.run_id.clone(),
        source_implementation: checkpoint.implementation.clone(),
        raw_fact_fingerprint: fingerprint,
        provenance: "provider_evaluation".into(),
        layers,
    })
}

fn review_intent_descriptions(intents: &[Intent]) -> Vec<String> {
    let mut ordered: Vec<_> = intents.iter().collect();
    ordered.sort_by(|left, right| {
        left.timestamp_ns
            .cmp(&right.timestamp_ns)
            .then_with(|| left.intent_id.as_str().cmp(right.intent_id.as_str()))
    });
    ordered
        .into_iter()
        .filter(|intent| !intent.tool_name.eq_ignore_ascii_case("UserPrompt"))
        .filter_map(|intent| {
            intent
                .tool_args
                .iter()
                .find_map(|arg| match arg {
                    NormalizedArg::Content(content) if !content.is_empty() => {
                        Some(format!("{}: {}", intent.tool_name, content))
                    }
                    _ => None,
                })
                .or_else(|| Some(intent.tool_name.to_string()))
        })
        .take(10)
        .collect()
}

fn review_alert_decision(
    decision: ObserverDecision,
) -> crate::responsibility::types::AlertDecision {
    match decision {
        ObserverDecision::Safe => crate::responsibility::types::AlertDecision::Safe,
        ObserverDecision::Unsafe => crate::responsibility::types::AlertDecision::Unsafe,
        ObserverDecision::Uncertain => crate::responsibility::types::AlertDecision::Uncertain,
    }
}

fn sha256_json<T: Serialize>(value: &T) -> Result<String, AblationError> {
    let bytes = serde_json::to_vec(value)
        .map_err(|error| AblationError::Serialization(error.to_string()))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

/// Rebuild the staircase from an explicit raw-fact view.
pub fn rebuild_from_raw_facts(raw: &RawFactInput<'_>) -> Result<AblationReport, AblationError> {
    rebuild_from_raw_facts_with_overrides(raw, &HashMap::new())
}

fn rebuild_from_raw_facts_with_overrides(
    raw: &RawFactInput<'_>,
    review_overrides: &HashMap<AblationLayer, Vec<Observation>>,
) -> Result<AblationReport, AblationError> {
    validate_raw_facts(raw)?;
    let units = identity_units_from_facts(raw.identity_input, raw.intents, raw.actions)?;

    // The low layers deliberately use the physical-process view. The full
    // resolver condition is only used by L3, where identity is one of the
    // components being added.
    let pid_view = IdentityView::resolve(
        raw.identity_input,
        &units,
        IdentityCondition::PidInstanceOnly,
        raw.identity_policy,
    )?;
    let pid_graph = rebuild_graph(raw, &pid_view, true)?;
    let pbc_graph = rebuild_graph(raw, &pid_view, false)?;

    let full_view = IdentityView::resolve(
        raw.identity_input,
        &units,
        IdentityCondition::Full,
        raw.identity_policy,
    )?;
    let full_graph = rebuild_graph(raw, &full_view, true)?;

    let mut layers = Vec::with_capacity(AblationLayer::ALL.len());
    layers.push(l0_report(raw));
    layers.push(l0p5_report(raw, &pid_view, &pbc_graph));
    layers.push(l1_report(raw, &pid_view, &pid_graph));
    let l2_observations = review_overrides
        .get(&AblationLayer::L2)
        .map(Vec::as_slice)
        .unwrap_or(raw.observations);
    let l3_observations = review_overrides
        .get(&AblationLayer::L3)
        .map(Vec::as_slice)
        .unwrap_or(raw.observations);
    layers.push(l2_report(raw, &pid_view, &pid_graph, l2_observations));
    layers.push(l3_report(raw, &full_view, &full_graph, l3_observations));

    // A historical checkpoint has no frozen graph context, so the computable
    // scores are diagnostic. A validated `Some` snapshot — including an empty
    // one — closes that specific gap; unrelated partial states stay.
    if raw.graph_inputs.is_none() {
        for layer in layers.iter_mut().skip(1) {
            layer.state = AblationState::Partial;
            let reason = "runtime_graph_context_not_frozen";
            layer.unavailable_reason = Some(match layer.unavailable_reason.take() {
                Some(previous) => format!("{previous}; {reason}"),
                None => reason.into(),
            });
        }
    }

    Ok(AblationReport {
        schema: ABLATION_SCHEMA.into(),
        protocol: ABLATION_PROTOCOL.into(),
        run_id: String::new(),
        source_fingerprint: String::new(),
        raw_fact_fingerprint: raw_fact_fingerprint(raw)?,
        read_checkpoint_graph: false,
        read_checkpoint_identities: false,
        capture_status: quality_status(raw.capture_quality).to_string(),
        observer_enabled: raw.observer_enabled,
        review_cache_provenance: None,
        review_cache_sha256: None,
        layers,
        limitations: global_limitations(),
    })
}

fn validate_raw_facts(raw: &RawFactInput<'_>) -> Result<(), AblationError> {
    let intents: HashSet<_> = raw.intents.iter().map(|i| i.intent_id.as_str()).collect();
    let actions: HashSet<_> = raw.actions.iter().map(|a| a.action_id.as_str()).collect();
    let evidence: HashSet<_> = raw
        .evidence
        .iter()
        .map(|e| e.evidence_id.as_str())
        .collect();
    if intents.len() != raw.intents.len()
        || actions.len() != raw.actions.len()
        || evidence.len() != raw.evidence.len()
        || intents.contains("")
        || actions.contains("")
        || evidence.contains("")
    {
        return Err(AblationError::InvalidRawFacts(
            "duplicate or empty occurrence id".into(),
        ));
    }
    if raw
        .evidence
        .iter()
        .any(|e| !intents.contains(e.intent_id.as_str()) || !actions.contains(e.action_id.as_str()))
    {
        return Err(AblationError::InvalidRawFacts(
            "dangling correlation evidence".into(),
        ));
    }
    if let Some(inputs) = raw.graph_inputs {
        inputs
            .validate(raw.intents)
            .map_err(AblationError::InvalidRawFacts)?;
    }
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════════
//  Layer reports
// ═══════════════════════════════════════════════════════════════════════════

/// Fixed raw-activity index for L0.
///
/// One unit per raw OS action occurrence captured before correlation. Parsed
/// intents, correlation evidence, PBC, graph, identity and Observer facts are
/// never inputs. AUROC is invariant to monotone rescaling, so the zero point
/// and unit are the only declared properties; no calibrated weight or
/// probability transform is invented here.
fn l0_activity_index(raw: &RawFactInput<'_>) -> f64 {
    raw.actions.len() as f64
}

fn l0_report(raw: &RawFactInput<'_>) -> AblationLayerReport {
    AblationLayerReport {
        layer: AblationLayer::L0,
        state: AblationState::Available,
        unavailable_reason: None,
        visible: visible_facts(raw, None, false),
        identity_condition: None,
        graph: None,
        score: None,
        score_route: None,
        ranking_score: Some(l0_activity_index(raw)),
        score_kind: Some(CONTINUOUS_SCORE_KIND.into()),
        score_definition: Some(L0_ACTIVITY_SCORE_DEFINITION.into()),
        verdict: None,
        review_candidates: empty_review_candidates(),
        review_task_context: None,
        limitations: vec![
            "L0 reports the raw OS action-occurrence index and counts only; the index is a ranking value, not a calibrated detection probability".into(),
            "No identity, graph, pattern, rule or observer fact is visible".into(),
            "L0 has no decision threshold fixed before the run in this revision: AUROC is estimable from ranking_score, fixed-threshold TPR/FPR is not".into(),
        ],
    }
}

fn l0p5_report(
    raw: &RawFactInput<'_>,
    view: &IdentityView,
    graph: &ResponsibilityGraph,
) -> AblationLayerReport {
    let config = frozen_config(raw);
    let patterns = Vec::new();
    let rule_hits = Vec::new();
    let repetition = repetition_count(graph);
    let breakdown = compute_score_breakdown_with(
        &rule_hits,
        &patterns,
        graph,
        raw.evidence,
        repetition,
        raw.intents.len() as u32,
        raw.score_signals.clone(),
        &config.scoring,
    );
    let score_route = route_for(breakdown.total, &config);
    let ranking_score = f64::from(breakdown.total);
    AblationLayerReport {
        layer: AblationLayer::L0p5,
        state: AblationState::Available,
        unavailable_reason: None,
        visible: visible_facts(raw, Some(view), false),
        identity_condition: Some(IdentityCondition::PidInstanceOnly),
        graph: Some(graph_facts(graph, 0, 0, 0)),
        score: Some((breakdown, &config).into()),
        score_route: Some(score_route),
        ranking_score: Some(ranking_score),
        score_kind: Some(CONTINUOUS_SCORE_KIND.into()),
        score_definition: Some(SCORING_KERNEL.into()),
        verdict: None,
        review_candidates: empty_review_candidates(),
        review_task_context: None,
        limitations: vec![
            "Visible graph is limited to intent/action links and policy-boundary crossings".into(),
            "Rules, patterns, taint and observer facts are not visible".into(),
        ],
    }
}

fn l1_report(
    raw: &RawFactInput<'_>,
    view: &IdentityView,
    graph: &ResponsibilityGraph,
) -> AblationLayerReport {
    let config = frozen_config(raw);
    let patterns = patterns_for(raw, graph);
    let rule_hits = Vec::new();
    let repetition = repetition_count(graph);
    let breakdown = compute_score_breakdown_with(
        &rule_hits,
        &patterns,
        graph,
        raw.evidence,
        repetition,
        raw.intents.len() as u32,
        raw.score_signals.clone(),
        &config.scoring,
    );
    let score_route = route_for(breakdown.total, &config);
    let ranking_score = f64::from(breakdown.total);
    AblationLayerReport {
        layer: AblationLayer::L1,
        state: AblationState::Available,
        unavailable_reason: None,
        visible: visible_facts(raw, Some(view), false),
        identity_condition: Some(IdentityCondition::PidInstanceOnly),
        graph: Some(graph_facts(graph, patterns.len(), 0, 0)),
        score: Some((breakdown, &config).into()),
        score_route: Some(score_route),
        ranking_score: Some(ranking_score),
        score_kind: Some(CONTINUOUS_SCORE_KIND.into()),
        score_definition: Some(SCORING_KERNEL.into()),
        verdict: None,
        review_candidates: empty_review_candidates(),
        review_task_context: None,
        limitations: vec![
            "Graph and taint are rebuilt from frozen evidence links and intent arguments".into(),
            "The rule catalog is not visible at this layer".into(),
        ],
    }
}

fn l2_report(
    raw: &RawFactInput<'_>,
    view: &IdentityView,
    graph: &ResponsibilityGraph,
    observations: &[Observation],
) -> AblationLayerReport {
    let config = frozen_config(raw);
    let patterns = patterns_for(raw, graph);
    let rule_hits = Vec::new();
    let repetition = repetition_count(graph);
    let breakdown = compute_score_breakdown_with(
        &rule_hits,
        &patterns,
        graph,
        raw.evidence,
        repetition,
        raw.intents.len() as u32,
        raw.score_signals.clone(),
        &config.scoring,
    );

    let (mut state, mut reason) = match raw.observer_enabled {
        Some(true) => (AblationState::Available, None),
        Some(false) => (
            AblationState::Partial,
            Some("observer_disabled: L2 cannot observe semantic verdicts".to_string()),
        ),
        None => (
            AblationState::Partial,
            Some("observer_state_unknown: observer_enabled is absent".to_string()),
        ),
    };
    let (observations, cache_mismatch) =
        applicable_observations(raw, observations, graph, &patterns);
    let mut verdict = if raw.observer_enabled == Some(true) {
        Some(observer_verdict(raw, &observations, graph, &patterns, &[]))
    } else {
        None
    };
    if cache_mismatch > 0 {
        state = AblationState::Partial;
        let note = format!(
            "observer_cache_not_applicable: {cache_mismatch} scoped review candidate(s) do not match the rebuilt graph"
        );
        reason = Some(match reason {
            Some(existing) => format!("{existing}; {note}"),
            None => note.clone(),
        });
        if let Some(verdict) = verdict.as_mut() {
            verdict
                .provenance
                .push(format!("observer_cache_not_applicable:{cache_mismatch}"));
        }
    }

    let score_route = route_for(breakdown.total, &config);
    let ranking_score = f64::from(breakdown.total);
    AblationLayerReport {
        layer: AblationLayer::L2,
        state,
        unavailable_reason: reason,
        visible: visible_facts(raw, Some(view), true),
        identity_condition: Some(IdentityCondition::PidInstanceOnly),
        graph: Some(graph_facts(graph, patterns.len(), 0, 0)),
        score: Some((breakdown, &config).into()),
        score_route: Some(score_route),
        ranking_score: Some(ranking_score),
        score_kind: Some(CONTINUOUS_SCORE_KIND.into()),
        score_definition: Some(SCORING_KERNEL.into()),
        verdict,
        review_candidates: candidates_json(&current_candidates(raw, graph, &patterns)),
        review_task_context: Some(
            serde_json::to_value(observed_task_context(raw))
                .expect("observed task context is serializable"),
        ),
        limitations: vec![
            "Observer observations can change the verdict but not the continuous score".into(),
            "The rule catalog and full identity view are not visible at this layer".into(),
        ],
    }
}

fn l3_report(
    raw: &RawFactInput<'_>,
    view: &IdentityView,
    graph: &ResponsibilityGraph,
    observations: &[Observation],
) -> AblationLayerReport {
    let config = frozen_config(raw);
    let patterns = patterns_for(raw, graph);
    let engine = RuleEngine::with_workspace_prefix(raw.workspace_prefix.to_string());
    let (_, all_hits) = engine.evaluate(graph);
    let safe_rules = all_hits
        .iter()
        .filter(|hit| hit.decision == RuleDecision::Safe)
        .count();
    let unsafe_ids: Vec<String> = all_hits
        .iter()
        .filter(|hit| hit.decision == RuleDecision::Unsafe)
        .map(|hit| hit.rule_id.clone())
        .filter(|rule_id| rule_id != "hard.sensitive_egress")
        .collect();
    let repetition = repetition_count(graph);
    let breakdown = compute_score_breakdown_with(
        &all_hits,
        &patterns,
        graph,
        raw.evidence,
        repetition,
        raw.intents.len() as u32,
        raw.score_signals.clone(),
        &config.scoring,
    );

    let (observations, cache_mismatch) =
        applicable_observations(raw, observations, graph, &patterns);
    let mut verdict = if raw.observer_enabled != Some(true) && unsafe_ids.is_empty() {
        None
    } else {
        Some(observer_verdict(
            raw,
            &observations,
            graph,
            &patterns,
            &unsafe_ids,
        ))
    };
    // L3 is cumulative over L2: a disabled or unknown Observer is a missing
    // layer input, so L3 cannot be reported as Available from rules alone.
    let mut state = AblationState::Available;
    let mut reason = None;
    match raw.observer_enabled {
        Some(true) => {}
        Some(false) => {
            state = AblationState::Partial;
            reason = Some("observer_disabled: L3 cannot observe semantic verdicts".to_string());
        }
        None => {
            state = AblationState::Partial;
            reason = Some("observer_state_unknown: observer_enabled is absent".to_string());
        }
    }
    if cache_mismatch > 0 {
        state = AblationState::Partial;
        let note = format!(
            "observer_cache_not_applicable: {cache_mismatch} scoped review candidate(s) do not match the rebuilt graph"
        );
        reason = Some(match reason {
            Some(existing) => format!("{existing}; {note}"),
            None => note,
        });
        if let Some(verdict) = verdict.as_mut() {
            verdict
                .provenance
                .push(format!("observer_cache_not_applicable:{cache_mismatch}"));
        }
    }

    let score_route = route_for(breakdown.total, &config);
    let ranking_score = f64::from(breakdown.total);
    AblationLayerReport {
        layer: AblationLayer::L3,
        state,
        unavailable_reason: reason,
        visible: visible_facts(raw, Some(view), true),
        identity_condition: Some(IdentityCondition::Full),
        graph: Some(graph_facts(
            graph,
            patterns.len(),
            safe_rules,
            unsafe_ids.len(),
        )),
        score: Some((breakdown, &config).into()),
        score_route: Some(score_route),
        ranking_score: Some(ranking_score),
        score_kind: Some(CONTINUOUS_SCORE_KIND.into()),
        score_definition: Some(SCORING_KERNEL.into()),
        verdict,
        review_candidates: candidates_json(&current_candidates(raw, graph, &patterns)),
        review_task_context: Some(
            serde_json::to_value(observed_task_context(raw))
                .expect("observed task context is serializable"),
        ),
        limitations: vec![
            "Full identity is resolved from PRE-resolver facts, not checkpoint.identities".into(),
            "hard_sensitive_egress is forced off; the deterministic egress rule is not applied"
                .into(),
            "Observer state is a layer input: a disabled or unknown Observer keeps L3 Partial"
                .into(),
        ],
    }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Graph reconstruction
// ═══════════════════════════════════════════════════════════════════════════

fn rebuild_graph(
    raw: &RawFactInput<'_>,
    view: &IdentityView,
    context_propagation: bool,
) -> Result<ResponsibilityGraph, AblationError> {
    let provider = ViewIdentityProvider::new(view, raw.intents, raw.actions);
    let mut builder = GraphBuilder::new(GraphConfig::default());
    builder.set_deterministic_id_namespace(format!(
        "ablation:{}:{}:{context_propagation}",
        raw_fact_fingerprint(raw)?,
        view.condition.as_str()
    ));
    builder.set_context_propagation(context_propagation);
    builder.set_local_egress_review(raw.classification_config.local_egress);
    apply_frozen_graph_inputs(&mut builder, raw, &provider, context_propagation);

    let intents_by_id: HashMap<&str, &Intent> = raw
        .intents
        .iter()
        .map(|intent| (intent.intent_id.as_str(), intent))
        .collect();
    let actions_by_id: HashMap<&str, &Action> = raw
        .actions
        .iter()
        .map(|action| (action.action_id.as_str(), action))
        .collect();

    let mut evidence: Vec<&CorrelationEvidence> = raw.evidence.iter().collect();
    evidence.sort_by(|left, right| {
        left.timestamp_ns
            .cmp(&right.timestamp_ns)
            .then_with(|| left.evidence_id.as_str().cmp(right.evidence_id.as_str()))
            .then_with(|| left.intent_id.as_str().cmp(right.intent_id.as_str()))
            .then_with(|| left.action_id.as_str().cmp(right.action_id.as_str()))
    });
    for record in evidence {
        let (Some(intent), Some(action)) = (
            intents_by_id.get(record.intent_id.as_str()),
            actions_by_id.get(record.action_id.as_str()),
        ) else {
            // A dangling evidence row is a fact about the recording, not
            // something to synthesize. Keep it out of the graph.
            continue;
        };
        builder.handle(record, intent, action, &provider);
    }

    let mut intents: Vec<&Intent> = raw.intents.iter().collect();
    intents.sort_by(|left, right| {
        left.timestamp_ns
            .cmp(&right.timestamp_ns)
            .then_with(|| left.intent_id.as_str().cmp(right.intent_id.as_str()))
    });
    for intent in intents {
        builder.handle_intent_content(intent, &provider, intent.timestamp_ns);
        builder.handle_intent_pbc(intent, &provider);
    }

    if context_propagation {
        // Delegation endpoints are responsibility-graph facts (L1+), not PBC
        // facts (L0.5). Without this gate the PBC-only graph silently exposed
        // AgentDelegatesTo edges that L1 is supposed to add.
        record_rebound_delegations(&mut builder, raw, &provider);
    }

    Ok(builder.graph().clone())
}

/// Apply the frozen task envelopes and content provenance through the current
/// identity view, never through a stored authoritative actor id.
fn apply_frozen_graph_inputs(
    builder: &mut GraphBuilder,
    raw: &RawFactInput<'_>,
    provider: &dyn IdentityProvider,
    context_propagation: bool,
) {
    let Some(inputs) = raw.graph_inputs else {
        return;
    };
    let intents_by_id: HashMap<&str, &Intent> = raw
        .intents
        .iter()
        .map(|intent| (intent.intent_id.as_str(), intent))
        .collect();
    let mut envelopes: HashMap<AgentId, crate::responsibility::provenance::CapabilityEnvelope> =
        HashMap::new();
    for task in &inputs.task_envelopes {
        let Some(intent) = intents_by_id.get(task.intent_id.as_str()) else {
            continue;
        };
        // An envelope whose current view has no actor is unknown; do not invent
        // one. Multiple tasks that resolve to the same actor are intersected,
        // never unioned.
        let Some(agent) = provider.agent_for_intent(intent) else {
            continue;
        };
        envelopes
            .entry(agent)
            .and_modify(|current| *current = current.intersect(&task.envelope))
            .or_insert_with(|| task.envelope.clone());
    }
    builder.set_envelopes(envelopes);

    if context_propagation {
        for provenance in &inputs.provenance {
            builder.set_intent_provenance(
                &provenance.intent_id,
                provenance.tag.clone(),
                &provenance.span_id,
                &provenance.source,
            );
        }
    }
}

/// Re-resolve observed delegations by their process anchors.
///
/// Only called for context-enabled graphs (L1+); L0.5 is PBC-only.
///
/// Stored agent ids are phase-1/authoritative outputs and are deliberately
/// ignored. A parent or child the current view cannot bind (or binds both to
/// the same actor) produces no edge instead of a guessed one.
fn record_rebound_delegations(
    builder: &mut GraphBuilder,
    raw: &RawFactInput<'_>,
    provider: &dyn IdentityProvider,
) {
    let mut delegations: Vec<&DelegationObservation> = raw.delegations.iter().collect();
    delegations.sort_by(|left, right| {
        left.delegation_id
            .cmp(&right.delegation_id)
            .then_with(|| left.timestamp_ns.cmp(&right.timestamp_ns))
    });
    for delegation in delegations {
        let Some(from_agent) = provider.agent_for_process(ProcessId::new(
            delegation.parent_pid,
            delegation.parent_start_time_ns,
        )) else {
            continue;
        };
        let Some(to_agent) = provider.agent_for_process(ProcessId::new(
            delegation.child_pid,
            delegation.child_start_time_ns,
        )) else {
            continue;
        };
        if from_agent == to_agent {
            continue;
        }
        let mut rebound = delegation.clone();
        rebound.from_agent_id = from_agent.to_string();
        rebound.to_agent_id = to_agent.to_string();
        builder.record_delegation(rebound);
    }
}

/// Minimal `IdentityProvider` over a resolved ablation view.
///
/// The view returns actor partitions, not production `AgentId`s. Prefixing the
/// partition keeps the ablation graph self-contained and makes it impossible to
/// mistake these nodes for the checkpoint's authoritative identities.
struct ViewIdentityProvider {
    process_agents: HashMap<ProcessId, Option<AgentId>>,
    message_agents: HashMap<MessageId, AgentId>,
    intent_agents: HashMap<IntentId, AgentId>,
}

impl ViewIdentityProvider {
    fn new(view: &IdentityView, intents: &[Intent], actions: &[Action]) -> Self {
        let actions_by_id: HashMap<&str, &Action> = actions
            .iter()
            .map(|action| (action.action_id.as_str(), action))
            .collect();
        let intents_by_id: HashMap<&str, &Intent> = intents
            .iter()
            .map(|intent| (intent.intent_id.as_str(), intent))
            .collect();

        let mut process_agents: HashMap<ProcessId, Option<AgentId>> = HashMap::new();
        let mut message_agents = HashMap::new();
        let mut intent_agents = HashMap::new();

        for unit in &view.units {
            let Some(partition) = unit.actor.partition() else {
                continue;
            };
            let agent = AgentId::from(format!("ablation:{partition}"));
            match unit.kind {
                crate::responsibility::identity::IdentityUnitKind::Message => {
                    if let Some(message_id) = &unit.message_id {
                        message_agents.insert(message_id.clone(), agent);
                    }
                }
                crate::responsibility::identity::IdentityUnitKind::Intent => {
                    if let Some(intent_id) = unit.unit_id.strip_prefix("intent:")
                        && let Some(intent) = intents_by_id.get(intent_id)
                    {
                        intent_agents.insert(intent.intent_id.clone(), agent.clone());
                        merge_process_agent(&mut process_agents, intent.process_id, agent.clone());
                    }
                }
                crate::responsibility::identity::IdentityUnitKind::Action => {
                    if let Some(action_id) = unit.unit_id.strip_prefix("action:")
                        && let Some(action) = actions_by_id.get(action_id)
                    {
                        merge_process_agent(&mut process_agents, action.process_id, agent.clone());
                    }
                }
            }
        }

        Self {
            process_agents,
            message_agents,
            intent_agents,
        }
    }
}

fn merge_process_agent(
    map: &mut HashMap<ProcessId, Option<AgentId>>,
    process: ProcessId,
    agent: AgentId,
) {
    match map.get(&process) {
        None => {
            map.insert(process, Some(agent));
        }
        Some(Some(existing)) if existing != &agent => {
            // One physical process cannot be two actors in one view. Treat the
            // conflict as unknown rather than picking a winner.
            map.insert(process, None);
        }
        Some(_) => {}
    }
}

impl IdentityProvider for ViewIdentityProvider {
    fn agent_for_process(&self, process_id: ProcessId) -> Option<AgentId> {
        self.process_agents.get(&process_id).cloned().flatten()
    }

    fn agent_for_message(&self, message_id: MessageId) -> Option<AgentId> {
        self.message_agents.get(&message_id).cloned()
    }

    fn agent_for_intent(&self, intent: &Intent) -> Option<AgentId> {
        self.intent_agents
            .get(&intent.intent_id)
            .cloned()
            .or_else(|| self.agent_for_message(intent.message_id.clone()))
            .or_else(|| self.agent_for_process(intent.process_id))
    }

    fn all_assignments(&self) -> HashMap<ProcessId, AgentId> {
        self.process_agents
            .iter()
            .filter_map(|(process, agent)| agent.clone().map(|agent| (*process, agent)))
            .collect()
    }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Reporting helpers
// ═══════════════════════════════════════════════════════════════════════════

fn frozen_config(
    raw: &RawFactInput<'_>,
) -> crate::responsibility::observer::types::ClassificationConfig {
    let mut config = raw.classification_config.clone();
    // This is an analysis-protocol constant, not a policy decision.
    config.hard_sensitive_egress = false;
    config
}

fn patterns_for(
    raw: &RawFactInput<'_>,
    graph: &ResponsibilityGraph,
) -> Vec<crate::responsibility::graph::GraphPatternMatch> {
    if raw.disable_patterns {
        Vec::new()
    } else {
        GraphPatternMatcher::new(default_templates()).match_graph(graph)
    }
}

fn repetition_count(graph: &ResponsibilityGraph) -> u32 {
    graph
        .all_edges()
        .filter(|edge| edge.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
        .map(|edge| edge.evidence_refs.len() as u32)
        .max()
        .unwrap_or(0)
}

fn route_for(
    score: f32,
    config: &crate::responsibility::observer::types::ClassificationConfig,
) -> String {
    if score > config.threshold_unsafe {
        "direct_unsafe".into()
    } else if score < config.threshold_safe {
        "direct_safe".into()
    } else {
        "ambiguous_review".into()
    }
}

fn visible_facts(
    raw: &RawFactInput<'_>,
    view: Option<&IdentityView>,
    observer_visible: bool,
) -> VisibleFacts {
    let processes: HashSet<ProcessId> = raw
        .identity_input
        .processes
        .iter()
        .map(|process| process.process_id)
        .collect();
    let (identity_units, identity_assigned, identity_unknown) = match view {
        Some(view) => (
            view.units.len(),
            view.units
                .iter()
                .filter(|unit| unit.actor.partition().is_some())
                .count(),
            view.units
                .iter()
                .filter(|unit| unit.actor.partition().is_none())
                .count(),
        ),
        None => (0, 0, 0),
    };
    VisibleFacts {
        intents: raw.intents.len(),
        actions: raw.actions.len(),
        evidence: raw.evidence.len(),
        observations: if observer_visible {
            raw.observations.len()
        } else {
            0
        },
        processes: processes.len(),
        identity_units,
        identity_assigned,
        identity_unknown,
        observer_enabled: if observer_visible {
            raw.observer_enabled
        } else {
            None
        },
    }
}

fn graph_facts(
    graph: &ResponsibilityGraph,
    pattern_matches: usize,
    safe_rules: usize,
    unsafe_rules: usize,
) -> GraphFacts {
    let mut facts = GraphFacts {
        nodes: graph.node_count(),
        edges: graph.edge_count(),
        intent_action_edges: 0,
        pbc_edges: 0,
        evidenced_pbc_edges: 0,
        taint_edges: 0,
        context_edges: 0,
        delegation_edges: 0,
        competing_edges: 0,
        pattern_matches,
        safe_rules,
        unsafe_rules,
    };
    for edge in graph.all_edges() {
        match edge.edge_type {
            ResponsibilityEdgeType::IntentCausesAction => facts.intent_action_edges += 1,
            ResponsibilityEdgeType::PolicyBoundaryCrossing => {
                facts.pbc_edges += 1;
                if !edge.evidence_refs.is_empty() {
                    facts.evidenced_pbc_edges += 1;
                }
            }
            ResponsibilityEdgeType::TaintPropagatesTo
            | ResponsibilityEdgeType::TaintPropagation => facts.taint_edges += 1,
            ResponsibilityEdgeType::ContextFlowsFrom
            | ResponsibilityEdgeType::ContextInheritance => facts.context_edges += 1,
            ResponsibilityEdgeType::AgentDelegatesTo
            | ResponsibilityEdgeType::Delegation
            | ResponsibilityEdgeType::AuthorityTransfer => facts.delegation_edges += 1,
            ResponsibilityEdgeType::CompetingExplanation => facts.competing_edges += 1,
            _ => {}
        }
    }
    facts
}

fn observer_verdict(
    raw: &RawFactInput<'_>,
    applicable: &[Observation],
    graph: &ResponsibilityGraph,
    patterns: &[crate::responsibility::graph::GraphPatternMatch],
    unsafe_rules: &[String],
) -> FinalVerdict {
    let mut observations = applicable.to_vec();
    if !unsafe_rules.is_empty() {
        observations.push(Observation::HardViolation {
            rule_ids: unsafe_rules.to_vec(),
        });
    }
    let mut verdict = aggregate_final_decision(&observations);

    // Production refuses to call an unreviewed current candidate safe. Rebuild
    // the same pending set from raw facts so L2/L3 cannot turn a missing review
    // into a Safe verdict. Only applicable reviews may mark a candidate reviewed:
    // a cached review dropped by `applicable_observations` must not suppress the
    // pending check for its individually-matching half.
    let pending = pending_candidates(raw, applicable, graph, patterns);
    if !pending.is_empty() && verdict.decision == crate::responsibility::types::AlertDecision::Safe
    {
        verdict.decision = crate::responsibility::types::AlertDecision::Uncertain;
        verdict.severity = crate::responsibility::types::AlertSeverity::Warning;
        verdict.authority = crate::responsibility::types::AlertAuthority::Unavailable;
        verdict.reasoning =
            "Current candidate facts still require review; reviewed facts alone cannot establish a safe run."
                .into();
        verdict
            .provenance
            .push(format!("pending_candidate_reviews:{}", pending.len()));
    }

    let status = quality_status(raw.capture_quality);
    if status != "healthy" {
        verdict.provenance.push(format!("capture_quality:{status}"));
        if verdict.decision == crate::responsibility::types::AlertDecision::Safe {
            verdict.decision = crate::responsibility::types::AlertDecision::Uncertain;
            verdict.severity = crate::responsibility::types::AlertSeverity::Warning;
            verdict.authority = crate::responsibility::types::AlertAuthority::Unavailable;
            verdict.reasoning =
                "Capture completeness is unproven or degraded; absence of a detected violation cannot establish a safe run."
                    .into();
        }
    }
    verdict
}

fn observed_task_context(
    raw: &RawFactInput<'_>,
) -> crate::responsibility::observer::types::ObservedTaskContext {
    let mut intents = raw.intents.to_vec();
    intents.sort_by(|left, right| {
        left.timestamp_ns
            .cmp(&right.timestamp_ns)
            .then_with(|| left.intent_id.as_str().cmp(right.intent_id.as_str()))
    });
    crate::responsibility::observer::types::ObservedTaskContext::from_intents(&intents)
}

fn current_candidates(
    raw: &RawFactInput<'_>,
    graph: &ResponsibilityGraph,
    patterns: &[crate::responsibility::graph::GraphPatternMatch],
) -> Vec<CandidateSummary> {
    let mut candidates =
        graph_candidates_with(graph, patterns, raw.classification_config.sensitive_egress);
    enrich_candidate_outcomes(&mut candidates, raw.evidence, raw.actions);
    bind_task_context(
        &mut candidates,
        observed_task_context(raw).cache_fingerprint(),
    );
    candidates
}

fn candidates_json(candidates: &[CandidateSummary]) -> serde_json::Value {
    serde_json::to_value(candidates).unwrap_or_else(|_| empty_review_candidates())
}

fn pending_candidates(
    raw: &RawFactInput<'_>,
    observations: &[Observation],
    graph: &ResponsibilityGraph,
    patterns: &[crate::responsibility::graph::GraphPatternMatch],
) -> Vec<CandidateSummary> {
    unreviewed_candidates_of(observations, current_candidates(raw, graph, patterns))
}

/// Split Observer observations into those that may be reused on the rebuilt
/// candidate graph and those that may not.
///
/// Production candidate versions include graph node/edge ids. Rebuilding below
/// the resolver creates new ids, so a review is applicable only when *every*
/// candidate ref in that review still exists. A partially matching review is
/// dropped as a whole: reusing only its surviving candidates would attach a
/// verdict to a cache key that no longer exists. Dropped refs are counted so the
/// layer can report `observer_cache_not_applicable`; they are never silently
/// treated as a reviewed-safe verdict or as proof that no review happened.
fn applicable_observations(
    raw: &RawFactInput<'_>,
    observations: &[Observation],
    graph: &ResponsibilityGraph,
    patterns: &[crate::responsibility::graph::GraphPatternMatch],
) -> (Vec<Observation>, usize) {
    let candidates = current_candidates(raw, graph, patterns);
    let current: HashSet<_> = candidates
        .iter()
        .map(|candidate| candidate.candidate.clone())
        .collect();

    let mut reused = Vec::with_capacity(observations.len());
    let mut dropped = 0usize;
    for observation in observations {
        match observation {
            Observation::ScopedReview { candidates, .. } => {
                if !candidates.is_empty()
                    && candidates
                        .iter()
                        .all(|candidate| current.contains(candidate))
                {
                    reused.push(observation.clone());
                } else {
                    dropped += candidates.len().max(1);
                }
            }
            Observation::ScopedCandidate { candidate } if current.contains(candidate) => {
                reused.push(observation.clone());
            }
            // Whole-run safe/uncertain/failed verdicts carry no graph candidate
            // refs, so a rebuilt graph cannot invalidate them.
            Observation::ObserverSafe { .. }
            | Observation::ObserverUncertain { .. }
            | Observation::ObserverFailed { .. } => {
                reused.push(observation.clone());
            }
            // Legacy unscoped reviews cannot bind to the rebuilt facts. Hard
            // violations are recomputed from this layer's own enabled rules;
            // importing them would undo hard_sensitive_egress=false.
            _ => dropped += 1,
        }
    }
    (reused, dropped)
}

fn global_limitations() -> Vec<String> {
    vec![
        "Low layers never read checkpoint.graph or checkpoint.identities".into(),
        "The graph is rebuilt from frozen evidence links, intents, actions, delegated process anchors and the optional frozen graph inputs; historical checkpoints without that snapshot stay partial".into(),
        "Observer observations can change a verdict but not the continuous system score".into(),
        "Scoped Observer reviews whose candidate id/version does not match the rebuilt graph are marked observer_cache_not_applicable, never silently assumed applicable".into(),
        "hard_sensitive_egress is forced off at every layer".into(),
        "L0 is an activity-count baseline: ranking_score is a fixed raw-OS action-occurrence index for AUROC ranking, not a calibrated detection probability or a fixed-threshold route".into(),
        "L0.5-L3 ranking_score equals the frozen kernel system_score; score_kind is continuous for every layer".into(),
        "This report is not an experimental result and does not establish detection efficacy".into(),
    ]
}

fn raw_fact_fingerprint(raw: &RawFactInput<'_>) -> Result<String, AblationError> {
    let mut hasher = Sha256::new();
    hasher.update(ABLATION_PROTOCOL.as_bytes());
    hash_value(&mut hasher, &raw.identity_input)?;
    hash_value(&mut hasher, &raw.identity_policy)?;
    hash_sequence(&mut hasher, raw.intents)?;
    hash_sequence(&mut hasher, raw.actions)?;
    hash_sequence(&mut hasher, raw.evidence)?;
    hash_sequence(&mut hasher, raw.observations)?;
    hash_sequence(&mut hasher, raw.delegations)?;
    hash_value(&mut hasher, &raw.score_signals)?;
    hash_value(&mut hasher, &raw.classification_config)?;
    hash_value(&mut hasher, &raw.workspace_prefix)?;
    hash_value(&mut hasher, &raw.disable_patterns)?;
    hash_value(&mut hasher, &raw.observer_enabled)?;
    hash_value(&mut hasher, raw.capture_quality)?;
    hash_value(&mut hasher, &raw.graph_inputs)?;
    Ok(hex::encode(hasher.finalize()))
}

fn hash_value<T: Serialize>(hasher: &mut Sha256, value: &T) -> Result<(), AblationError> {
    let bytes =
        serde_json::to_vec(value).map_err(|err| AblationError::Serialization(err.to_string()))?;
    hasher.update((bytes.len() as u64).to_le_bytes());
    hasher.update(bytes);
    Ok(())
}

fn hash_sequence<T: Serialize>(hasher: &mut Sha256, values: &[T]) -> Result<(), AblationError> {
    let mut rows = Vec::with_capacity(values.len());
    for value in values {
        rows.push(
            serde_json::to_vec(value)
                .map_err(|err| AblationError::Serialization(err.to_string()))?,
        );
    }
    rows.sort();
    hasher.update((rows.len() as u64).to_le_bytes());
    for row in rows {
        hasher.update((row.len() as u64).to_le_bytes());
        hasher.update(row);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::identity::{IdentityBoundaryPolicy, RawIdentityInput};
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
    use crate::responsibility::types::{
        ActionCandidateType, ActionId, ActionOutcome, AgentId, CandidateDerivationMode, EvidenceId,
        IntentAuthoritySource, IntentClass, IntentParseState, MatchLevel, MessageId, NormalizedArg,
        PhaseIndicator, ProcessId, SignalClass,
    };
    use serde_json::json;

    fn process(pid: u32, start: u64) -> ProcessId {
        ProcessId::new(pid, start)
    }

    fn identity_input() -> RawIdentityInput {
        let mut graph = SignalGraph::new();
        let agent = process(100, 1);
        let tool = process(200, 2);
        graph.observe_exec(
            agent,
            process(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        graph.observe_exec(
            tool,
            agent,
            ProcessExecInfo {
                comm: "cat".into(),
                ..Default::default()
            },
            None,
            2,
        );
        graph.observe_message_direct(tool, MessageId::from("msg-tool"));
        RawIdentityInput::from_snapshot(&graph.snapshot(), Vec::new())
    }

    fn intent(id: &str, process: ProcessId, message: &str, path: &str, ts: u64) -> Intent {
        Intent {
            intent_id: IntentId::from(id),
            process_id: process,
            message_id: MessageId::from(message),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path(path.into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: ts,
            native_tool_call: None,
            declared_conversation_id: None,
        }
    }

    fn action(
        id: &str,
        process: ProcessId,
        action_type: ActionCandidateType,
        args: Vec<NormalizedArg>,
        ts: u64,
    ) -> Action {
        Action {
            outcome: ActionOutcome::Succeeded,
            syscall_ret: Some(0),
            syscall_name: Some("openat".into()),
            action_id: ActionId::from(id),
            process_id: process,
            action_type,
            normalized_args: args,
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: ts,
        }
    }

    fn evidence(id: &str, intent: &Intent, action: &Action, ts: u64) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::from(id),
            intent_id: intent.intent_id.clone(),
            action_id: action.action_id.clone(),
            match_level: MatchLevel::L1Exact,
            confidence: 0.95,
            matched_fields: Default::default(),
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Provisional { timestamp_ns: ts },
            timestamp_ns: ts,
        }
    }

    fn checkpoint() -> AnalysisCheckpoint {
        let tool = process(200, 2);
        let read = intent("intent-read", tool, "msg-tool", "/etc/shadow", 10);
        let read_action = action(
            "action-read",
            tool,
            ActionCandidateType::FileRead,
            vec![NormalizedArg::Path("/etc/shadow".into())],
            11,
        );
        let link = evidence("evidence-read", &read, &read_action, 11);
        AnalysisCheckpoint {
            run_id: "run-ablation".into(),
            contract: crate::responsibility::replay::checkpoint::REPLAY_CONTRACT.into(),
            implementation: crate::responsibility::replay::checkpoint::implementation_fingerprint(),
            graph: crate::responsibility::types::GraphSnapshot {
                snapshot_id: "frozen-final".into(),
                timestamp_ns: 99,
                nodes: Vec::new(),
                edges: Vec::new(),
                metrics: Default::default(),
            },
            identity_input: Some(identity_input()),
            identity_policy: Some(IdentityBoundaryPolicy::new(["claude"])),
            identities: Vec::new(),
            evidence: vec![link],
            intents: vec![read],
            actions: vec![read_action],
            delegations: Vec::new(),
            score_signals: Default::default(),
            observations: Vec::new(),
            disable_patterns: false,
            classification_config: Default::default(),
            workspace_prefix: "/home/agent/project".into(),
            fallback_agent_id: Some(AgentId::from("agent-a")),
            timeline: Default::default(),
            capture_quality: json!({
                "status": "healthy",
                "sources": {
                    "Process:100": {
                        "started": true,
                        "attached": 1,
                        "final_stats_seen": true,
                        "drain_complete": true,
                        "exit_status": 0,
                        "lost": 0,
                        "rate_limited": 0,
                        "initial_snapshot_complete": true
                    }
                },
                "analysis": {"event_counts": {"process": 2}}
            }),
            observer_enabled: Some(true),
            graph_refinements: Vec::new(),
            identity_refinements: Vec::new(),
            evidence_refinements: Vec::new(),
            non_link_count: 0,
            captured_request_contexts: None,
            graph_inputs: None,
        }
    }

    #[test]
    fn legacy_checkpoint_without_identity_input_is_rejected() {
        let mut checkpoint = checkpoint();
        checkpoint.identity_input = None;
        assert_eq!(
            rebuild(&checkpoint).unwrap_err(),
            AblationError::UnavailableIdentityInput
        );
        checkpoint.identity_input = Some(identity_input());
        checkpoint.identity_policy = None;
        assert_eq!(
            rebuild(&checkpoint).unwrap_err(),
            AblationError::UnavailableIdentityPolicy
        );
    }

    #[test]
    fn low_layers_ignore_the_frozen_graph_and_identities() {
        let mut checkpoint = checkpoint();
        let baseline = rebuild(&checkpoint).unwrap();

        checkpoint.graph.nodes = vec![crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("injected"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("injected-agent")),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 1,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1,
                overrides: None,
            },
        }];
        checkpoint.identities = vec![crate::responsibility::types::AuthoritativeIdentity {
            agent_id: AgentId::from("injected-agent"),
            process_ids: vec![process(200, 2)],
            message_ids: vec![MessageId::from("msg-tool")],
            tls_sessions: Vec::new(),
            confidence: crate::responsibility::types::AgentConfidence::Authoritative,
            provisional_refs: Vec::new(),
        }];
        let mutated = rebuild(&checkpoint).unwrap();

        assert_eq!(
            baseline.raw_fact_fingerprint, mutated.raw_fact_fingerprint,
            "the fingerprint must cover raw facts only"
        );
        assert_eq!(
            serde_json::to_value(&baseline.layers).unwrap(),
            serde_json::to_value(&mutated.layers).unwrap(),
            "no layer may read checkpoint.graph or checkpoint.identities"
        );
    }

    #[test]
    fn staircase_adds_one_component_at_a_time() {
        let report = rebuild(&checkpoint()).unwrap();
        assert_eq!(
            report
                .layers
                .iter()
                .map(|layer| layer.layer)
                .collect::<Vec<_>>(),
            AblationLayer::ALL.to_vec()
        );
        assert_eq!(report.layers[0].score, None, "L0 has no detection score");
        assert_eq!(report.layers[0].graph, None, "L0 has no graph");
        assert!(
            report.layers[1].graph.as_ref().unwrap().pbc_edges > 0,
            "L0.5 must expose the policy-boundary crossings: {:?}",
            report.layers[1]
        );
        let l0p5_edges = report.layers[1].graph.as_ref().unwrap().edges;
        let l1_edges = report.layers[2].graph.as_ref().unwrap().edges;
        assert!(
            l1_edges >= l0p5_edges,
            "L1 cannot hide graph facts visible at L0.5"
        );
        assert_eq!(
            report.layers[2].score.as_ref().unwrap().system_score,
            report.layers[3].score.as_ref().unwrap().system_score,
            "Observer observations must not silently change the continuous score"
        );
        assert_eq!(report.layers[2].score_route, report.layers[3].score_route);
        assert!(report.layers.iter().all(|layer| {
            layer
                .score
                .as_ref()
                .is_none_or(|score| !score.hard_sensitive_egress)
        }));
    }

    #[test]
    fn observer_state_is_not_collapsed_into_absence() {
        let mut disabled = checkpoint();
        disabled.observer_enabled = Some(false);
        let report = rebuild(&disabled).unwrap();
        let l2 = &report.layers[3];
        assert_eq!(l2.layer, AblationLayer::L2);
        assert_eq!(l2.state, AblationState::Partial);
        assert!(
            l2.unavailable_reason
                .as_deref()
                .is_some_and(|reason| reason.starts_with("observer_disabled"))
        );
        assert_eq!(l2.verdict, None);

        let mut unknown = checkpoint();
        unknown.observer_enabled = None;
        let report = rebuild(&unknown).unwrap();
        let l2 = &report.layers[3];
        assert_eq!(l2.state, AblationState::Partial);
        assert!(
            l2.unavailable_reason
                .as_deref()
                .is_some_and(|reason| reason.starts_with("observer_state_unknown"))
        );

        let report = rebuild(&checkpoint()).unwrap();
        let l2 = &report.layers[3];
        assert_eq!(l2.state, AblationState::Partial);
        assert_eq!(
            l2.unavailable_reason.as_deref(),
            Some("runtime_graph_context_not_frozen")
        );
    }

    #[test]
    fn unscoped_observer_verdict_cannot_authorize_rebuilt_facts() {
        let mut with_observer = checkpoint();
        with_observer.observations = vec![Observation::ObserverUnsafe {
            confidence: 0.9,
            cited: 1,
        }];
        let report = rebuild(&with_observer).unwrap();
        let l2 = &report.layers[3];
        assert_eq!(l2.state, AblationState::Partial);
        assert_ne!(
            l2.verdict.as_ref().map(|verdict| verdict.decision.clone()),
            Some(crate::responsibility::types::AlertDecision::Unsafe)
        );
        assert_eq!(
            l2.score.as_ref().unwrap().system_score,
            report.layers[2].score.as_ref().unwrap().system_score
        );
    }

    #[test]
    fn hard_sensitive_egress_is_forced_off() {
        let mut checkpoint = checkpoint();
        checkpoint.classification_config.hard_sensitive_egress = true;
        let report = rebuild(&checkpoint).unwrap();
        assert!(
            report
                .layers
                .iter()
                .filter_map(|layer| layer.score.as_ref())
                .all(|score| !score.hard_sensitive_egress)
        );
        let l3 = &report.layers[4];
        assert!(l3.verdict.as_ref().is_none_or(|verdict| {
            !verdict
                .provenance
                .iter()
                .any(|p| p.contains("hard.sensitive_egress"))
        }));
    }

    #[test]
    fn low_layers_do_not_expose_observer_state_or_count() {
        let mut checkpoint = checkpoint();
        checkpoint.observations = vec![Observation::ObserverUnsafe {
            confidence: 0.9,
            cited: 1,
        }];
        let report = rebuild(&checkpoint).unwrap();
        for index in (0..=2).rev() {
            assert_eq!(report.layers[index].visible.observations, 0);
            assert_eq!(report.layers[index].visible.observer_enabled, None);
        }
        let l2 = &report.layers[3];
        assert_eq!(l2.visible.observations, 1);
        assert_eq!(l2.visible.observer_enabled, Some(true));
        assert_eq!(report.layers[0].visible.identity_units, 0);
        assert_eq!(report.layers[0].visible.identity_assigned, 0);
    }

    #[test]
    fn partially_matching_review_is_dropped_as_a_whole_and_cannot_clear_pending() {
        let checkpoint = checkpoint();
        let raw = RawFactInput::from_checkpoint(&checkpoint).unwrap();
        let units =
            identity_units_from_facts(raw.identity_input, raw.intents, raw.actions).unwrap();
        let view = IdentityView::resolve(
            raw.identity_input,
            &units,
            IdentityCondition::PidInstanceOnly,
            raw.identity_policy,
        )
        .unwrap();
        let graph = rebuild_graph(&raw, &view, true).unwrap();
        let patterns = patterns_for(&raw, &graph);
        let pending = pending_candidates(&raw, &[], &graph, &patterns);
        assert!(
            !pending.is_empty(),
            "fixture must expose pending candidates"
        );
        let real = pending[0].candidate.clone();

        let mut reviewed = checkpoint.clone();
        reviewed.observations = vec![Observation::ScopedReview {
            candidates: vec![
                real,
                crate::responsibility::classification::candidates::CandidateRef {
                    id: "operation:foreign".into(),
                    version: "production-version".into(),
                },
            ],
            decision: crate::responsibility::types::AlertDecision::Safe,
            confidence: 0.9,
            cited: 1,
        }];
        let reviewed_raw = RawFactInput::from_checkpoint(&reviewed).unwrap();
        let (kept, dropped) =
            applicable_observations(&reviewed_raw, reviewed_raw.observations, &graph, &patterns);
        assert!(kept.is_empty(), "a mixed review must be dropped as a whole");
        assert_eq!(dropped, 2);
        assert_eq!(
            pending_candidates(&reviewed_raw, &kept, &graph, &patterns).len(),
            pending.len(),
            "the matching half of a dropped review must stay pending"
        );
    }

    #[test]
    fn scoped_review_cache_mismatch_is_explicit_partial_not_silent() {
        let mut checkpoint = checkpoint();
        checkpoint.observations = vec![Observation::ScopedReview {
            candidates: vec![
                crate::responsibility::classification::candidates::CandidateRef {
                    id: "operation:production-edge-id".into(),
                    version: "production-version".into(),
                },
            ],
            decision: crate::responsibility::types::AlertDecision::Safe,
            confidence: 0.9,
            cited: 1,
        }];
        let report = rebuild(&checkpoint).unwrap();
        let l2 = &report.layers[3];
        assert_eq!(l2.state, AblationState::Partial);
        assert!(
            l2.unavailable_reason
                .as_deref()
                .is_some_and(|reason| reason.starts_with("observer_cache_not_applicable"))
        );
        let verdict = l2
            .verdict
            .as_ref()
            .expect("L2 keeps an explicitly partial verdict");
        assert_ne!(
            verdict.decision,
            crate::responsibility::types::AlertDecision::Safe,
            "an unmatched reviewed-safe cache must not make the layer safe"
        );
        assert!(
            verdict
                .provenance
                .iter()
                .any(|item| item.starts_with("observer_cache_not_applicable:"))
        );
    }

    #[test]
    fn disabled_hard_rule_cannot_leak_from_cached_observations() {
        let mut input = checkpoint();
        input.observations = vec![Observation::HardViolation {
            rule_ids: vec!["hard.sensitive_egress".into()],
        }];
        let report = rebuild(&input).unwrap();
        for layer in &report.layers[3..] {
            assert!(layer.verdict.as_ref().is_none_or(|v| {
                !v.provenance
                    .iter()
                    .any(|p| p.contains("hard.sensitive_egress"))
            }));
        }
    }

    #[test]
    fn empty_scoped_review_cannot_authorize_rebuilt_candidates() {
        let mut input = checkpoint();
        input.observations = vec![Observation::ScopedReview {
            candidates: vec![],
            decision: crate::responsibility::types::AlertDecision::Unsafe,
            confidence: 0.99,
            cited: 1,
        }];
        let report = rebuild(&input).unwrap();
        assert_eq!(report.layers[3].state, AblationState::Partial);
        assert_ne!(
            report.layers[3].verdict.as_ref().unwrap().decision,
            crate::responsibility::types::AlertDecision::Unsafe
        );
    }

    #[test]
    fn dangling_raw_evidence_is_rejected_instead_of_scored() {
        let mut input = checkpoint();
        input.actions.clear();
        assert!(rebuild(&input).is_err());
    }

    #[test]
    fn duplicate_raw_identity_units_are_rejected_instead_of_last_wins() {
        let mut input = checkpoint();
        input.intents.push(input.intents[0].clone());
        assert!(rebuild(&input).is_err());
    }

    #[test]
    fn graph_layers_without_runtime_context_are_not_admitted_as_full_rebuilds() {
        let report = rebuild(&checkpoint()).unwrap();
        for layer in report.layers.iter().skip(1) {
            assert_eq!(layer.state, AblationState::Partial);
            assert!(
                layer
                    .unavailable_reason
                    .as_deref()
                    .unwrap()
                    .contains("runtime_graph_context_not_frozen")
            );
        }
    }

    #[test]
    fn fingerprint_is_stable_across_input_order() {
        let mut checkpoint = checkpoint();
        let first = rebuild(&checkpoint).unwrap();
        checkpoint.intents.reverse();
        checkpoint.actions.reverse();
        checkpoint.evidence.reverse();
        let second = rebuild(&checkpoint).unwrap();
        assert_eq!(first.raw_fact_fingerprint, second.raw_fact_fingerprint);
    }

    // ── P1 frozen graph input closure ─────────────────────────────────

    fn frozen_context_checkpoint() -> AnalysisCheckpoint {
        use crate::responsibility::provenance::{CapabilityEnvelope, ProvenanceTag};
        use crate::responsibility::replay::checkpoint::{
            FROZEN_GRAPH_INPUTS_VERSION, FrozenGraphInputs, FrozenIntentProvenance,
            FrozenTaskEnvelope,
        };
        use std::collections::HashSet;

        let mut checkpoint = checkpoint();
        let tool = process(200, 2);
        let parent_prompt = Intent {
            intent_id: IntentId::from("intent-parent-task"),
            process_id: process(100, 1),
            message_id: MessageId::from("msg-parent"),
            tool_name: "UserPrompt".into(),
            tool_args: vec![NormalizedArg::Content("Draft the summary.".into())],
            intent_class: IntentClass::Unknown,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 5,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        let prompt = Intent {
            intent_id: IntentId::from("intent-task"),
            process_id: tool,
            message_id: MessageId::from("msg-tool"),
            tool_name: "UserPrompt".into(),
            tool_args: vec![NormalizedArg::Content("Summarize the document.".into())],
            intent_class: IntentClass::Unknown,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 9,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        let net_intent = Intent {
            intent_id: IntentId::from("intent-net"),
            process_id: tool,
            message_id: MessageId::from("msg-tool"),
            tool_name: "WebFetch".into(),
            tool_args: vec![NormalizedArg::Url {
                host: "evil.example".into(),
                port: Some(443),
            }],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 12,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        let net_action = action(
            "action-net",
            tool,
            ActionCandidateType::NetworkConnect,
            vec![NormalizedArg::Url {
                host: "evil.example".into(),
                port: Some(443),
            }],
            13,
        );
        let net_evidence = evidence("evidence-net", &net_intent, &net_action, 13);
        checkpoint.intents = vec![parent_prompt, prompt.clone(), net_intent.clone()];
        checkpoint.actions = vec![net_action];
        checkpoint.evidence = vec![net_evidence];
        checkpoint.delegations = vec![DelegationObservation {
            delegation_id: "delegation:parent-child".into(),
            from_agent_id: "stale-parent".into(),
            to_agent_id: "stale-child".into(),
            parent_pid: 100,
            parent_start_time_ns: 1,
            child_pid: 200,
            child_start_time_ns: 2,
            timestamp_ns: 20,
            child_comm: "cat".into(),
            confidence: 0.8,
        }];
        checkpoint.graph_inputs = Some(FrozenGraphInputs {
            version: FROZEN_GRAPH_INPUTS_VERSION,
            task_envelopes: vec![FrozenTaskEnvelope {
                intent_id: prompt.intent_id.clone(),
                envelope: CapabilityEnvelope {
                    tools: HashSet::from(["Read".to_string()]),
                    network_hosts: HashSet::new(),
                    write_prefixes: vec!["/frozen/project/".into()],
                    shell_authorized: false,
                    source: "user_task".into(),
                },
            }],
            provenance: vec![FrozenIntentProvenance {
                intent_id: net_intent.intent_id.clone(),
                tag: ProvenanceTag::retrieved_untrusted(false),
                span_id: "span-net".into(),
                source: "tool_result:fetch".into(),
            }],
        });
        checkpoint
    }

    fn detached_delegation_checkpoint() -> AnalysisCheckpoint {
        let mut checkpoint = frozen_context_checkpoint();
        let parent = process(100, 1);
        let child = process(300, 3);
        let mut graph = SignalGraph::new();
        for (process_id, comm) in [(parent, "claude"), (child, "cat")] {
            graph.observe_exec(
                process_id,
                process(1, 0),
                ProcessExecInfo {
                    comm: comm.into(),
                    ..Default::default()
                },
                None,
                1,
            );
            graph.observe_message_direct(
                process_id,
                MessageId::from(format!("msg-{}", process_id.pid)),
            );
        }
        checkpoint.identity_input = Some(RawIdentityInput::from_snapshot(
            &graph.snapshot(),
            vec![(parent, child)],
        ));
        checkpoint.delegations[0].child_pid = child.pid;
        checkpoint.delegations[0].child_start_time_ns = child.start_time_ns;
        checkpoint.intents.push(Intent {
            intent_id: IntentId::from("intent-child-read"),
            process_id: child,
            message_id: MessageId::from("msg-300"),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path("/tmp/child.txt".into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 30,
            native_tool_call: None,
            declared_conversation_id: None,
        });
        checkpoint
    }

    fn graph_for_condition(
        checkpoint: &AnalysisCheckpoint,
        context_propagation: bool,
        condition: IdentityCondition,
    ) -> ResponsibilityGraph {
        let raw = RawFactInput::from_checkpoint(checkpoint).unwrap();
        let units =
            identity_units_from_facts(raw.identity_input, raw.intents, raw.actions).unwrap();
        let view =
            IdentityView::resolve(raw.identity_input, &units, condition, raw.identity_policy)
                .unwrap();
        rebuild_graph(&raw, &view, context_propagation).unwrap()
    }

    fn boundary_nodes(graph: &ResponsibilityGraph, tool_name: &str) -> usize {
        graph
            .all_nodes()
            .filter(|node| {
                matches!(
                    &node.resource,
                    Some(crate::responsibility::types::NormalizedResource::ToolCall {
                        tool_name: actual,
                        ..
                    }) if actual == tool_name
                )
            })
            .count()
    }

    fn delegation_endpoints(graph: &ResponsibilityGraph) -> Vec<(String, String)> {
        let mut endpoints: Vec<(String, String)> = graph
            .all_edges()
            .filter(|edge| edge.edge_type == ResponsibilityEdgeType::AgentDelegatesTo)
            .filter_map(|edge| {
                let source = graph.get_node(&edge.source)?.agent_id.as_ref()?.to_string();
                let target = graph.get_node(&edge.target)?.agent_id.as_ref()?.to_string();
                Some((source, target))
            })
            .collect();
        endpoints.sort();
        endpoints
    }

    #[test]
    fn frozen_envelope_fingerprint_survives_json_round_trip() {
        let mut checkpoint = frozen_context_checkpoint();
        let envelope = &mut checkpoint.graph_inputs.as_mut().unwrap().task_envelopes[0].envelope;
        envelope.tools = (0..20).map(|i| format!("tool-{i}")).collect();
        envelope.network_hosts = (0..20).map(|i| format!("host-{i}.example")).collect();
        let fingerprint = rebuild(&checkpoint).unwrap().raw_fact_fingerprint;
        let json = serde_json::to_string(&checkpoint).unwrap();
        for _ in 0..8 {
            let restored: AnalysisCheckpoint = serde_json::from_str(&json).unwrap();
            assert_eq!(
                fingerprint,
                rebuild(&restored).unwrap().raw_fact_fingerprint
            );
        }
    }

    #[test]
    fn multi_task_review_context_and_candidates_follow_canonical_observation_order() {
        let checkpoint = frozen_context_checkpoint();
        let first = rebuild(&checkpoint).unwrap();
        let mut shuffled = checkpoint.clone();
        shuffled.intents.reverse();
        let second = rebuild(&shuffled).unwrap();
        for index in [3, 4] {
            assert_eq!(
                first.layers[index].review_candidates,
                second.layers[index].review_candidates
            );
            assert_eq!(
                first.layers[index].review_task_context,
                second.layers[index].review_task_context
            );
            assert!(
                !first.layers[index].review_task_context.as_ref().unwrap()["messages"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
        }
    }

    #[test]
    fn empty_frozen_graph_inputs_clear_only_the_context_gap() {
        use crate::responsibility::replay::checkpoint::FrozenGraphInputs;

        let mut checkpoint = checkpoint();
        checkpoint.graph_inputs = Some(FrozenGraphInputs::empty());
        let report = rebuild(&checkpoint).unwrap();
        for layer in &report.layers[1..3] {
            assert_eq!(layer.state, AblationState::Available, "{layer:?}");
            assert!(
                layer
                    .unavailable_reason
                    .as_deref()
                    .is_none_or(|reason| !reason.contains("runtime_graph_context_not_frozen")),
                "a validated frozen snapshot resolves the context gap: {layer:?}"
            );
        }
    }

    #[test]
    fn frozen_envelope_changes_the_rebuilt_graph() {
        let frozen = graph_for_condition(
            &frozen_context_checkpoint(),
            true,
            IdentityCondition::PidInstanceOnly,
        );
        assert_eq!(
            boundary_nodes(&frozen, "CapabilityExpansion"),
            1,
            "the frozen task envelope must widen the graph with a capability boundary"
        );

        let mut without = frozen_context_checkpoint();
        without.graph_inputs = None;
        let plain = graph_for_condition(&without, true, IdentityCondition::PidInstanceOnly);
        assert_eq!(
            boundary_nodes(&plain, "CapabilityExpansion"),
            0,
            "without the frozen envelope no capability boundary may be invented"
        );
    }

    #[test]
    fn frozen_workspace_prefix_authorizes_only_the_frozen_surface() {
        let mut checkpoint = frozen_context_checkpoint();
        checkpoint.graph_inputs.as_mut().unwrap().provenance.clear();
        checkpoint.actions[0].action_type = ActionCandidateType::FileWrite;
        checkpoint.actions[0].normalized_args =
            vec![NormalizedArg::Path("/frozen/project/out.txt".into())];
        checkpoint.intents[1].tool_args =
            vec![NormalizedArg::Path("/frozen/project/out.txt".into())];
        let inside = graph_for_condition(&checkpoint, true, IdentityCondition::PidInstanceOnly);
        assert_eq!(
            boundary_nodes(&inside, "CapabilityExpansion"),
            0,
            "the frozen workspace prefix must authorize its own surface"
        );

        checkpoint.actions[0].normalized_args =
            vec![NormalizedArg::Path("/elsewhere/out.txt".into())];
        checkpoint.intents[1].tool_args = vec![NormalizedArg::Path("/elsewhere/out.txt".into())];
        let outside = graph_for_condition(&checkpoint, true, IdentityCondition::PidInstanceOnly);
        assert_eq!(
            boundary_nodes(&outside, "CapabilityExpansion"),
            1,
            "the replay must not re-derive the workspace from the current environment"
        );
    }

    #[test]
    fn provenance_and_delegations_rebind_from_the_current_view_only() {
        let checkpoint = frozen_context_checkpoint();
        let context = graph_for_condition(&checkpoint, true, IdentityCondition::PidInstanceOnly);
        assert_eq!(
            boundary_nodes(&context, "UntrustedInstructionFlow"),
            1,
            "frozen provenance must create the boundary on a context-enabled graph"
        );
        let endpoints = delegation_endpoints(&context);
        assert_eq!(
            endpoints.len(),
            1,
            "the PID-anchored delegation must survive"
        );
        assert!(
            endpoints[0].0.starts_with("ablation:") && endpoints[0].1.starts_with("ablation:"),
            "delegation endpoints must come from the current view, not stored agent ids: {endpoints:?}"
        );
        assert!(
            !endpoints[0].0.contains("stale") && !endpoints[0].1.contains("stale"),
            "stored agent ids must be ignored: {endpoints:?}"
        );

        let policy_only =
            graph_for_condition(&checkpoint, false, IdentityCondition::PidInstanceOnly);
        assert_eq!(
            boundary_nodes(&policy_only, "UntrustedInstructionFlow"),
            0,
            "provenance belongs only to the context-enabled graph"
        );

        let detached = detached_delegation_checkpoint();
        let full = graph_for_condition(&detached, true, IdentityCondition::Full);
        let full_endpoints = delegation_endpoints(&full);
        assert_eq!(
            full_endpoints.len(),
            1,
            "a delegation between actors that stay distinct must survive another identity condition"
        );
        assert_ne!(full_endpoints[0].0, full_endpoints[0].1);
        assert!(
            !full_endpoints[0].0.contains("stale") && !full_endpoints[0].1.contains("stale"),
            "the other condition still must not read stored agent ids: {full_endpoints:?}"
        );

        let mut renamed = frozen_context_checkpoint();
        renamed.delegations[0].from_agent_id = "other-stale".into();
        renamed.delegations[0].to_agent_id = "another-stale".into();
        assert_eq!(
            delegation_endpoints(&graph_for_condition(
                &renamed,
                true,
                IdentityCondition::PidInstanceOnly
            )),
            endpoints,
            "changing the stored agent ids must not change the delegation"
        );

        let mut unknown = frozen_context_checkpoint();
        unknown.delegations[0].child_pid = 9999;
        assert!(
            delegation_endpoints(&graph_for_condition(
                &unknown,
                true,
                IdentityCondition::PidInstanceOnly
            ))
            .is_empty(),
            "an unknown process anchor must not be guessed into a delegation"
        );
    }

    #[test]
    fn malformed_frozen_graph_inputs_are_rejected() {
        use crate::responsibility::replay::checkpoint::FrozenIntentProvenance;

        let mut bad_version = frozen_context_checkpoint();
        bad_version.graph_inputs.as_mut().unwrap().version += 1;
        assert!(matches!(
            rebuild(&bad_version).unwrap_err(),
            AblationError::InvalidRawFacts(_)
        ));

        let mut dangling = frozen_context_checkpoint();
        dangling.graph_inputs.as_mut().unwrap().provenance[0].intent_id =
            IntentId::from("intent-missing");
        assert!(matches!(
            rebuild(&dangling).unwrap_err(),
            AblationError::InvalidRawFacts(_)
        ));

        let mut bad_task = frozen_context_checkpoint();
        bad_task.graph_inputs.as_mut().unwrap().task_envelopes[0].intent_id =
            IntentId::from("intent-net");
        assert!(
            matches!(
                rebuild(&bad_task).unwrap_err(),
                AblationError::InvalidRawFacts(_)
            ),
            "an envelope must anchor to a source UserPrompt intent"
        );

        let mut duplicate_task = frozen_context_checkpoint();
        let duplicate = duplicate_task.graph_inputs.as_ref().unwrap().task_envelopes[0].clone();
        duplicate_task
            .graph_inputs
            .as_mut()
            .unwrap()
            .task_envelopes
            .push(duplicate);
        assert!(matches!(
            rebuild(&duplicate_task).unwrap_err(),
            AblationError::InvalidRawFacts(_)
        ));

        let mut duplicate_provenance = frozen_context_checkpoint();
        let duplicate: FrozenIntentProvenance = duplicate_provenance
            .graph_inputs
            .as_ref()
            .unwrap()
            .provenance[0]
            .clone();
        duplicate_provenance
            .graph_inputs
            .as_mut()
            .unwrap()
            .provenance
            .push(duplicate);
        assert!(matches!(
            rebuild(&duplicate_provenance).unwrap_err(),
            AblationError::InvalidRawFacts(_)
        ));
    }

    fn fully_frozen_checkpoint() -> AnalysisCheckpoint {
        use crate::responsibility::replay::checkpoint::FrozenGraphInputs;
        let mut checkpoint = checkpoint();
        checkpoint.graph_inputs = Some(FrozenGraphInputs::empty());
        checkpoint
    }

    fn candidates_of(report: &AblationReport, layer: AblationLayer) -> Vec<CandidateSummary> {
        let row = report
            .layers
            .iter()
            .find(|row| row.layer == layer)
            .expect("layer");
        serde_json::from_value(row.review_candidates.clone()).unwrap()
    }

    fn scoped_cache(
        report: &AblationReport,
        layer: AblationLayer,
        candidates: Vec<CandidateSummary>,
    ) -> AblationReviewCache {
        AblationReviewCache {
            schema: ABLATION_REVIEW_CACHE_SCHEMA.into(),
            run_id: "run-ablation".into(),
            source_implementation: report.source_fingerprint.clone(),
            raw_fact_fingerprint: report.raw_fact_fingerprint.clone(),
            provenance: "external_unverified".into(),
            layers: vec![AblationReviewLayer {
                layer,
                observations: vec![Observation::ScopedReview {
                    candidates: candidates.into_iter().map(|row| row.candidate).collect(),
                    decision: crate::responsibility::types::AlertDecision::Safe,
                    confidence: 0.9,
                    cited: 1,
                }],
                metadata: None,
            }],
        }
    }

    struct CountingReviewProvider {
        inputs: std::sync::Mutex<Vec<crate::responsibility::classification::types::ObserverInput>>,
        outputs: std::sync::Mutex<
            std::collections::VecDeque<crate::responsibility::observer::types::ObserverOutput>,
        >,
        fail: bool,
    }

    impl CountingReviewProvider {
        fn new(outputs: Vec<crate::responsibility::observer::types::ObserverOutput>) -> Self {
            Self {
                inputs: std::sync::Mutex::new(Vec::new()),
                outputs: std::sync::Mutex::new(outputs.into()),
                fail: false,
            }
        }

        fn failing() -> Self {
            Self {
                inputs: std::sync::Mutex::new(Vec::new()),
                outputs: std::sync::Mutex::new(std::collections::VecDeque::new()),
                fail: true,
            }
        }

        fn call_count(&self) -> usize {
            self.inputs.lock().unwrap().len()
        }
    }

    #[async_trait::async_trait]
    impl ObserverProvider for CountingReviewProvider {
        async fn evaluate(
            &self,
            input: &crate::responsibility::classification::types::ObserverInput,
        ) -> Result<
            crate::responsibility::observer::types::ObserverOutput,
            crate::responsibility::observer::provider::ObserverProviderError,
        > {
            self.inputs.lock().unwrap().push(input.clone());
            if self.fail {
                return Err(
                    crate::responsibility::observer::provider::ObserverProviderError::Timeout,
                );
            }
            self.outputs
                .lock()
                .unwrap()
                .pop_front()
                .ok_or(crate::responsibility::observer::provider::ObserverProviderError::Timeout)
        }
    }

    fn safe_observer_output() -> crate::responsibility::observer::types::ObserverOutput {
        use crate::responsibility::claims::ClaimSeverity;
        use crate::responsibility::observer::types::{AlertAuthority, CacheTTL, ObserverDecision};
        crate::responsibility::observer::types::ObserverOutput {
            decision: ObserverDecision::Safe,
            severity: ClaimSeverity::Info,
            confidence: 0.8,
            cache_ttl: CacheTTL::Task,
            authority: AlertAuthority::Supporting,
            reasoning: "synthetic provider".into(),
            attacked_flag: false,
            evidence_cited: Vec::new(),
            rule_verdicts: Vec::new(),
            pattern_verdicts: Vec::new(),
            candidate_verdicts: Vec::new(),
            review_provenance: Vec::new(),
        }
    }

    #[tokio::test]
    async fn provider_review_uses_same_bounded_inputs_and_l2_has_no_catalog() {
        let checkpoint = fully_frozen_checkpoint();
        let base = rebuild(&checkpoint).unwrap();
        let provider =
            CountingReviewProvider::new(vec![safe_observer_output(), safe_observer_output()]);
        let cache = review_with_provider(&checkpoint, &provider).await.unwrap();
        assert_eq!(provider.call_count(), 2, "L2 and L3 each review once");
        assert_eq!(cache.provenance, "provider_evaluation");
        assert_eq!(
            cache
                .layers
                .iter()
                .map(|layer| layer.layer)
                .collect::<Vec<_>>(),
            vec![AblationLayer::L2, AblationLayer::L3]
        );
        for layer in &cache.layers {
            let metadata = layer.metadata.as_ref().expect("provider metadata");
            assert_eq!(metadata.mode, "provider_evaluation");
            assert_eq!(metadata.call_count, 1);
            assert_eq!(metadata.input_sha256.len(), 64);
            assert_eq!(metadata.response_sha256.len(), 64);
            assert_eq!(metadata.token_usage, None);
            assert_eq!(metadata.cost_usd, None);
        }

        let inputs = provider.inputs.lock().unwrap();
        let l2 = inputs[0].content_signals.as_ref().unwrap();
        let l3 = inputs[1].content_signals.as_ref().unwrap();
        let expected_l2 = candidates_of(&base, AblationLayer::L2);
        let expected_l3 = candidates_of(&base, AblationLayer::L3);
        assert_eq!(
            l2.candidates
                .iter()
                .map(|row| row.candidate.clone())
                .collect::<Vec<_>>(),
            expected_l2
                .iter()
                .take(MAX_REVIEW_CANDIDATES)
                .map(|row| row.candidate.clone())
                .collect::<Vec<_>>()
        );
        assert_eq!(
            l3.candidates
                .iter()
                .map(|row| row.candidate.clone())
                .collect::<Vec<_>>(),
            expected_l3
                .iter()
                .take(MAX_REVIEW_CANDIDATES)
                .map(|row| row.candidate.clone())
                .collect::<Vec<_>>()
        );
        assert_eq!(l2.observed_task, l3.observed_task);
        assert!(l2.observed_task.is_some());
        assert!(l2.rule_ids.is_empty(), "L2 must not see catalog rules");
        assert!(
            inputs[0].signature.rule_hits.is_empty(),
            "L2 must not see catalog rule hits"
        );
        assert!(
            !l3.rule_ids.is_empty(),
            "L3 keeps the full catalog: {:?}",
            l3.rule_ids
        );

        let reviewed = rebuild_with_reviews(&checkpoint, &cache).unwrap();
        for layer in [&reviewed.layers[3], &reviewed.layers[4]] {
            assert_eq!(layer.state, AblationState::Available, "{layer:?}");
            assert!(layer.verdict.as_ref().is_none_or(|verdict| {
                !verdict
                    .provenance
                    .iter()
                    .any(|item| item.starts_with("pending_candidate_reviews:"))
            }));
        }
    }

    #[tokio::test]
    async fn provider_reviews_all_candidates_in_bounded_batches_with_canonical_task() {
        let mut checkpoint = frozen_context_checkpoint();
        let base_intent = checkpoint
            .intents
            .iter()
            .find(|i| i.tool_name != "UserPrompt")
            .unwrap()
            .clone();
        let base_action = checkpoint.actions[0].clone();
        for index in 0..(MAX_REVIEW_CANDIDATES + 3) {
            let mut intent = base_intent.clone();
            intent.intent_id = IntentId::from(format!("batch-intent-{index}"));
            intent.tool_args = vec![NormalizedArg::Url {
                host: format!("egress-{index}.example"),
                port: Some(443),
            }];
            let mut action = base_action.clone();
            action.action_id = ActionId::from(format!("batch-action-{index}"));
            action.normalized_args = intent.tool_args.clone();
            checkpoint.evidence.push(evidence(
                &format!("batch-evidence-{index}"),
                &intent,
                &action,
                30 + index as u64,
            ));
            checkpoint.intents.push(intent);
            checkpoint.actions.push(action);
        }
        checkpoint.intents.reverse();
        let expected = rebuild(&checkpoint).unwrap();
        let expected_calls: usize = [AblationLayer::L2, AblationLayer::L3]
            .iter()
            .map(|layer| {
                candidates_of(&expected, *layer)
                    .len()
                    .div_ceil(MAX_REVIEW_CANDIDATES)
            })
            .sum();
        assert!(
            expected_calls > 2,
            "fixture must exceed one batch per layer"
        );
        let provider = CountingReviewProvider::new(vec![safe_observer_output(); expected_calls]);
        let cache = review_with_provider(&checkpoint, &provider).await.unwrap();
        assert_eq!(provider.call_count(), expected_calls);
        let inputs = provider.inputs.lock().unwrap();
        for input in inputs.iter() {
            let context = input
                .content_signals
                .as_ref()
                .unwrap()
                .observed_task
                .as_ref()
                .unwrap();
            assert_eq!(
                serde_json::to_value(context).unwrap(),
                expected.layers[3].review_task_context.clone().unwrap()
            );
        }
        let reviewed = rebuild_with_reviews(&checkpoint, &cache).unwrap();
        assert_eq!(reviewed.layers[3].state, AblationState::Available);
        assert_eq!(reviewed.layers[4].state, AblationState::Available);
    }

    #[tokio::test]
    async fn invalid_raw_facts_prevent_provider_calls() {
        let mut checkpoint = fully_frozen_checkpoint();
        checkpoint.actions.clear();
        let provider = CountingReviewProvider::new(vec![safe_observer_output(); 2]);
        assert!(review_with_provider(&checkpoint, &provider).await.is_err());
        assert_eq!(provider.call_count(), 0);
    }

    #[test]
    fn cache_rejects_invalid_confidence_before_evaluation() {
        let checkpoint = fully_frozen_checkpoint();
        let base = rebuild(&checkpoint).unwrap();
        for confidence in [-0.1, 1.5, f32::NAN] {
            let mut cache = scoped_cache(
                &base,
                AblationLayer::L2,
                candidates_of(&base, AblationLayer::L2),
            );
            for observation in &mut cache.layers[0].observations {
                if let Observation::ScopedReview {
                    confidence: value, ..
                } = observation
                {
                    *value = confidence;
                }
            }
            assert!(rebuild_with_reviews(&checkpoint, &cache).is_err());
        }
    }

    #[tokio::test]
    async fn provider_review_rejects_preconditions_before_calling() {
        let invalid_context = {
            let mut checkpoint = fully_frozen_checkpoint();
            checkpoint.graph_inputs = None;
            checkpoint
        };
        let disabled = {
            let mut checkpoint = fully_frozen_checkpoint();
            checkpoint.observer_enabled = Some(false);
            checkpoint
        };
        let degraded = {
            let mut checkpoint = fully_frozen_checkpoint();
            checkpoint.capture_quality = serde_json::json!({"status": "degraded"});
            checkpoint
        };
        for checkpoint in [invalid_context, disabled, degraded] {
            let provider = CountingReviewProvider::new(vec![safe_observer_output()]);
            assert!(review_with_provider(&checkpoint, &provider).await.is_err());
            assert_eq!(provider.call_count(), 0);
        }

        let provider = CountingReviewProvider::failing();
        assert!(
            review_with_provider(&fully_frozen_checkpoint(), &provider)
                .await
                .is_err()
        );

        let mut bad_confidence = safe_observer_output();
        bad_confidence.confidence = f32::NAN;
        let provider = CountingReviewProvider::new(vec![bad_confidence]);
        assert!(
            review_with_provider(&fully_frozen_checkpoint(), &provider)
                .await
                .is_err()
        );

        let mut unscoped = safe_observer_output();
        unscoped.decision = crate::responsibility::observer::types::ObserverDecision::Unsafe;
        let provider = CountingReviewProvider::new(vec![unscoped]);
        assert!(
            review_with_provider(&fully_frozen_checkpoint(), &provider)
                .await
                .is_err()
        );
    }

    #[test]
    fn repeated_reconstruction_has_identical_candidate_refs_after_roundtrip() {
        let checkpoint = fully_frozen_checkpoint();
        let first = rebuild(&checkpoint).unwrap();
        assert!(
            !candidates_of(&first, AblationLayer::L3).is_empty(),
            "fixture must expose a review candidate"
        );
        let encoded = serde_json::to_string(&checkpoint).unwrap();
        let mut roundtrip: AnalysisCheckpoint = serde_json::from_str(&encoded).unwrap();
        roundtrip.intents.reverse();
        roundtrip.actions.reverse();
        roundtrip.evidence.reverse();
        roundtrip.delegations.reverse();
        roundtrip.observations.reverse();
        let second = rebuild(&roundtrip).unwrap();
        for layer in AblationLayer::ALL {
            assert_eq!(
                first
                    .layers
                    .iter()
                    .find(|row| row.layer == layer)
                    .unwrap()
                    .review_candidates,
                second
                    .layers
                    .iter()
                    .find(|row| row.layer == layer)
                    .unwrap()
                    .review_candidates,
                "candidate refs must be deterministic at {}",
                layer.as_str()
            );
        }
    }

    #[test]
    fn same_layer_cache_clears_only_that_layer_cache_partial() {
        let mut checkpoint = fully_frozen_checkpoint();
        checkpoint.observations = vec![Observation::ScopedReview {
            candidates: vec![
                crate::responsibility::classification::candidates::CandidateRef {
                    id: "operation:foreign".into(),
                    version: "stale".into(),
                },
            ],
            decision: crate::responsibility::types::AlertDecision::Safe,
            confidence: 0.9,
            cited: 1,
        }];
        let base = rebuild(&checkpoint).unwrap();
        assert_eq!(base.layers[3].state, AblationState::Partial);
        assert!(
            base.layers[3]
                .unavailable_reason
                .as_deref()
                .unwrap()
                .contains("observer_cache_not_applicable")
        );
        assert_eq!(base.layers[4].state, AblationState::Partial);

        let cache = scoped_cache(
            &base,
            AblationLayer::L2,
            candidates_of(&base, AblationLayer::L2),
        );
        let reviewed = rebuild_with_reviews(&checkpoint, &cache).unwrap();
        assert_eq!(reviewed.layers[3].state, AblationState::Available);
        assert!(reviewed.layers[3].unavailable_reason.is_none());
        assert_eq!(
            reviewed.layers[4].state,
            AblationState::Partial,
            "another layer must keep its own unmatched cache"
        );
        assert!(
            reviewed.layers[4]
                .unavailable_reason
                .as_deref()
                .unwrap()
                .contains("observer_cache_not_applicable")
        );
        assert_eq!(
            base.layers[0].review_candidates,
            reviewed.layers[0].review_candidates
        );
        assert_eq!(
            base.layers[1].review_candidates,
            reviewed.layers[1].review_candidates
        );
        assert_eq!(
            base.layers[2].review_candidates,
            reviewed.layers[2].review_candidates
        );
    }

    #[test]
    fn stale_cached_candidate_versions_cannot_make_a_layer_safe() {
        let checkpoint = fully_frozen_checkpoint();
        let base = rebuild(&checkpoint).unwrap();
        let mut candidates = candidates_of(&base, AblationLayer::L2);
        for candidate in &mut candidates {
            candidate.candidate.version.push_str("-stale");
        }
        let cache = scoped_cache(&base, AblationLayer::L2, candidates);
        let reviewed = rebuild_with_reviews(&checkpoint, &cache).unwrap();
        assert_eq!(reviewed.layers[3].state, AblationState::Partial);
        assert_ne!(
            reviewed.layers[3]
                .verdict
                .as_ref()
                .map(|verdict| verdict.decision.clone()),
            Some(crate::responsibility::types::AlertDecision::Safe)
        );
    }

    #[test]
    fn invalid_review_cache_bindings_and_layers_are_rejected() {
        let checkpoint = fully_frozen_checkpoint();
        let base = rebuild(&checkpoint).unwrap();
        let valid = scoped_cache(
            &base,
            AblationLayer::L2,
            candidates_of(&base, AblationLayer::L2),
        );

        let mut wrong_run = valid.clone();
        wrong_run.run_id = "run-other".into();
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &wrong_run).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));

        let mut wrong_fingerprint = valid.clone();
        wrong_fingerprint.raw_fact_fingerprint = "0".repeat(64);
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &wrong_fingerprint).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));

        let mut wrong_implementation = valid.clone();
        wrong_implementation.source_implementation = "other".into();
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &wrong_implementation).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));

        let mut wrong_layer = valid.clone();
        wrong_layer.layers[0].layer = AblationLayer::L1;
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &wrong_layer).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));

        let mut duplicate = valid.clone();
        duplicate.layers.push(duplicate.layers[0].clone());
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &duplicate).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));

        let mut hard_rule = valid.clone();
        hard_rule.layers[0].observations = vec![Observation::HardViolation {
            rule_ids: vec!["hard.sensitive_egress".into()],
        }];
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &hard_rule).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));

        let mut empty_review = valid;
        empty_review.layers[0].observations = vec![Observation::ScopedReview {
            candidates: Vec::new(),
            decision: crate::responsibility::types::AlertDecision::Safe,
            confidence: 0.9,
            cited: 0,
        }];
        assert!(matches!(
            rebuild_with_reviews(&checkpoint, &empty_review).unwrap_err(),
            AblationError::InvalidReviewCache(_)
        ));
    }

    #[test]
    fn full_reconstruction_is_invariant_to_checkpoint_graph_and_identities() {
        let mut checkpoint = frozen_context_checkpoint();
        let baseline = rebuild(&checkpoint).unwrap();

        checkpoint.graph.nodes = vec![crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("injected"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("injected-agent")),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 1,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1,
                overrides: None,
            },
        }];
        checkpoint.identities = vec![crate::responsibility::types::AuthoritativeIdentity {
            agent_id: AgentId::from("injected-agent"),
            process_ids: vec![process(200, 2)],
            message_ids: vec![MessageId::from("msg-tool")],
            tls_sessions: Vec::new(),
            confidence: crate::responsibility::types::AgentConfidence::Authoritative,
            provisional_refs: Vec::new(),
        }];
        let mutated = rebuild(&checkpoint).unwrap();

        assert_eq!(
            baseline.raw_fact_fingerprint, mutated.raw_fact_fingerprint,
            "the fingerprint covers frozen graph inputs, not the final graph"
        );
    }

    // ── 2026-10-28 adversarial audit (P1) ─────────────────────────────

    #[test]
    fn pbc_layer_does_not_expose_delegation_edges_reserved_for_graph_layer() {
        // Contract §9: L0.5 adds PBC rules only; responsibility-graph
        // delegation/taint facts belong to L1. The frozen-context fixture has
        // one observed process-spawn delegation (parent 100 -> child 200) with
        // two distinct actors under the PID-only view.
        let checkpoint = frozen_context_checkpoint();
        let report = rebuild(&checkpoint).unwrap();
        let l0p5 = report.layers[1].graph.as_ref().expect("L0.5 graph");
        assert_eq!(
            l0p5.delegation_edges, 0,
            "L0.5 must not expose delegation edges: {l0p5:?}"
        );
        assert_eq!(l0p5.taint_edges, 0, "L0.5 must not expose taint: {l0p5:?}");
        assert_eq!(
            l0p5.context_edges, 0,
            "L0.5 must not expose context-flow edges: {l0p5:?}"
        );
        let l1 = report.layers[2].graph.as_ref().expect("L1 graph");
        assert_eq!(
            l1.delegation_edges, 1,
            "L1 must keep the observed delegation: {l1:?}"
        );
    }

    #[test]
    fn graph_layers_cannot_be_available_without_the_observer_signal() {
        let cases: [(Option<bool>, &str); 2] = [
            (Some(false), "observer_disabled"),
            (None, "observer_state_unknown"),
        ];
        for (observer, needle) in cases {
            let mut checkpoint = fully_frozen_checkpoint();
            checkpoint.observer_enabled = observer;
            let report = rebuild(&checkpoint).unwrap();
            assert_eq!(
                report.layers[3].state,
                AblationState::Partial,
                "L2 must stay Partial without an Observer: {observer:?}"
            );
            let l3 = &report.layers[4];
            assert_eq!(
                l3.state,
                AblationState::Partial,
                "L3 is cumulative over L2 and cannot turn Available without the Observer signal: {l3:?}"
            );
            assert!(
                l3.unavailable_reason
                    .as_deref()
                    .is_some_and(|reason| reason.contains(needle)),
                "the missing Observer input must stay explicit: {l3:?}"
            );
        }
    }

    #[test]
    fn l0_exposes_a_raw_activity_ranking_score_not_a_kernel_probability() {
        let checkpoint = fully_frozen_checkpoint();
        let raw_actions = checkpoint.actions.len() as f64;

        let baseline = rebuild(&checkpoint).unwrap();
        let l0 = &baseline.layers[0];
        assert_eq!(l0.score, None, "L0 must not fabricate a kernel breakdown");
        assert_eq!(
            l0.ranking_score,
            Some(raw_actions),
            "L0 score is the fixed raw-OS action-occurrence index: {l0:?}"
        );
        assert_eq!(l0.score_kind.as_deref(), Some("continuous"));
        assert!(
            l0.score_definition
                .as_deref()
                .is_some_and(|definition| definition.contains("activity")),
            "the activity index must be named so it cannot pass as a probability: {l0:?}"
        );
        for layer in &baseline.layers[1..] {
            assert_eq!(
                layer.ranking_score,
                layer
                    .score
                    .as_ref()
                    .map(|score| f64::from(score.system_score)),
                "score-based metrics must see the frozen kernel score at {}",
                layer.layer.as_str()
            );
            assert_eq!(layer.score_kind.as_deref(), Some("continuous"));
        }

        // Poisoning every authoritative field a low layer must never read
        // leaves the raw-activity index untouched.
        let mut poisoned = fully_frozen_checkpoint();
        poisoned
            .graph
            .nodes
            .push(crate::responsibility::types::ResponsibilityNode {
                node_id: crate::responsibility::types::NodeId::from("poison"),
                node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
                agent_id: Some(AgentId::from("poison-agent")),
                resource: None,
                evidence_refs: Vec::new(),
                timestamp_ns: 1,
                phase: PhaseIndicator::Authoritative {
                    timestamp_ns: 1,
                    overrides: None,
                },
            });
        poisoned.identities = vec![crate::responsibility::types::AuthoritativeIdentity {
            agent_id: AgentId::from("poison-agent"),
            process_ids: vec![process(200, 2)],
            message_ids: vec![MessageId::from("msg-tool")],
            tls_sessions: Vec::new(),
            confidence: crate::responsibility::types::AgentConfidence::Authoritative,
            provisional_refs: Vec::new(),
        }];
        poisoned.observations = vec![Observation::ObserverUnsafe {
            confidence: 0.99,
            cited: 9,
        }];
        poisoned.observer_enabled = Some(false);
        let poisoned_report = rebuild(&poisoned).unwrap();
        assert_eq!(
            poisoned_report.layers[0].ranking_score,
            Some(raw_actions),
            "L0 must not read graph/identities/observations/observer state"
        );
        assert_eq!(poisoned_report.layers[0].score, None);

        // The frozen graph context is not an L0 input either.
        let mut no_context = checkpoint.clone();
        no_context.graph_inputs = None;
        assert_eq!(
            rebuild(&no_context).unwrap().layers[0].ranking_score,
            Some(raw_actions)
        );

        // And the index changes only with the raw action count.
        let mut more_actions = fully_frozen_checkpoint();
        let mut extra = more_actions.actions[0].clone();
        extra.action_id = ActionId::from("action-extra");
        extra.timestamp_ns = 999;
        more_actions.actions.push(extra);
        let more = rebuild(&more_actions).unwrap();
        assert_eq!(
            more.layers[0].ranking_score,
            Some(raw_actions + 1.0),
            "one more raw action must increase the index by exactly one"
        );
    }
}
