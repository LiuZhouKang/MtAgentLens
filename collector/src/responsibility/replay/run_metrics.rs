// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// run_metrics.rs — versioned, serializable metrics for one authoritative run.
//
// The score is the classification kernel's actual continuous triage value. It
// is not a probability, not a label encoding, and never rewritten to agree
// with the final verdict. External execution references are explicit inputs we
// do not have here, so attack result flags stay unavailable/unreviewed.

use super::checkpoint::{AnalysisCheckpoint, identity_view_for_checkpoint, quality_status};
use crate::responsibility::classification::final_decision::FinalVerdict;
use crate::responsibility::classification::{ClassificationConfig, ClassificationResult};
use crate::responsibility::identity::{IdentityCondition, IdentityView};
use crate::responsibility::types::{ActionOutcome, AlertAuthority, AlertDecision, AlertSeverity};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const RUN_METRICS_KIND: &str = "run-metrics";
pub const RUN_METRICS_VERSION: u32 = 1;
pub const RUN_METRICS_SCHEMA: &str = "run-metrics/1";
pub const SYSTEM_SCORE_SOURCE: &str = "authoritative_classification_triage/1";
pub const REFERENCE_STATUS_UNREVIEWED: &str = "unreviewed";

/// Assigned/unknown counts for one existing identity cohort kind.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct KindCoverage {
    pub assigned: usize,
    pub unknown: usize,
    pub total: usize,
}

/// Per-kind coverage of the shared full-condition identity cohort.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct IdentityCoverage {
    pub condition: String,
    pub messages: KindCoverage,
    pub intents: KindCoverage,
    pub actions: KindCoverage,
}

impl IdentityCoverage {
    fn from_view(view: &IdentityView) -> Self {
        use crate::responsibility::identity::IdentityUnitKind;

        let mut coverage = Self {
            condition: view.condition.as_str().to_string(),
            ..Self::default()
        };
        for unit in &view.units {
            let slot = match unit.kind {
                IdentityUnitKind::Message => &mut coverage.messages,
                IdentityUnitKind::Intent => &mut coverage.intents,
                IdentityUnitKind::Action => &mut coverage.actions,
            };
            slot.total += 1;
            if unit.actor.partition().is_some() {
                slot.assigned += 1;
            } else {
                slot.unknown += 1;
            }
        }
        coverage
    }
}

/// Observed action completion counts. These describe captured syscall outcomes
/// only; they are not attack attempted/executed/succeeded references.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionOutcomeCounts {
    pub unknown: usize,
    pub attempted: usize,
    pub pending: usize,
    pub succeeded: usize,
    pub failed: usize,
    pub total: usize,
}

impl ActionOutcomeCounts {
    fn from_actions(actions: &[crate::responsibility::types::Action]) -> Self {
        let mut counts = Self::default();
        for action in actions {
            match action.outcome {
                ActionOutcome::Unknown => counts.unknown += 1,
                ActionOutcome::Attempted => counts.attempted += 1,
                ActionOutcome::Pending => counts.pending += 1,
                ActionOutcome::Succeeded => counts.succeeded += 1,
                ActionOutcome::Failed => counts.failed += 1,
            }
        }
        counts.total = actions.len();
        counts
    }
}

/// One versioned metrics row, persisted as `analysis_checkpoints(kind =
/// "run-metrics", version = 1)`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunMetrics {
    pub schema: String,
    pub schema_version: u32,
    pub run_id: String,

    /// Actual authoritative triage score, or `null` when it cannot be used.
    pub system_score: Option<f32>,
    pub system_score_unavailable_reason: Option<String>,
    /// This transform was never independently calibrated as a probability.
    pub score_is_probability: bool,
    pub calibrated_probability: Option<f32>,
    pub score_source: String,
    pub implementation_fingerprint: String,
    /// SHA-256 of the full serialized classification config, not environment.
    pub classification_config_sha256: String,
    /// Raw score fields retained for audit. `calibrated_score` inside remains a
    /// fixed Platt-shaped transform, not a validated probability.
    pub score_breakdown: serde_json::Value,

    pub final_decision: AlertDecision,
    pub final_severity: AlertSeverity,
    pub final_authority: AlertAuthority,
    pub capture_quality: String,

    /// `null` when the frozen raw input or policy is unavailable.
    pub identity_coverage: Option<IdentityCoverage>,
    pub identity_coverage_unavailable_reason: Option<String>,

    pub action_outcomes: ActionOutcomeCounts,
    /// Independent execution references. Never inferred from syscalls or alarms.
    pub attack_attempted: Option<bool>,
    pub attack_executed: Option<bool>,
    pub attack_succeeded: Option<bool>,
    pub reference_status: String,
    pub primary_eligible: bool,
    pub primary_eligibility_reason: String,
}

impl RunMetrics {
    /// Pure builder: no reference data, environment variables, ground truth or
    /// native SDK state. The verdict is recorded alongside, never mixed into
    /// the score.
    pub fn build(
        checkpoint: &AnalysisCheckpoint,
        classification: &ClassificationResult,
        verdict: &FinalVerdict,
    ) -> Self {
        let (system_score, system_score_unavailable_reason) =
            usable_system_score(classification.score);
        let (identity_coverage, identity_coverage_unavailable_reason) =
            match identity_view_for_checkpoint(checkpoint, IdentityCondition::Full) {
                Ok(view) => (Some(IdentityCoverage::from_view(&view)), None),
                Err(error) => (None, Some(error.to_string())),
            };
        Self {
            schema: RUN_METRICS_SCHEMA.to_string(),
            schema_version: RUN_METRICS_VERSION,
            run_id: checkpoint.run_id.clone(),
            system_score,
            system_score_unavailable_reason,
            score_is_probability: false,
            calibrated_probability: None,
            score_source: SYSTEM_SCORE_SOURCE.to_string(),
            implementation_fingerprint: checkpoint.implementation.clone(),
            classification_config_sha256: classification_config_sha256(
                &checkpoint.classification_config,
            ),
            score_breakdown: serde_json::to_value(&classification.score_breakdown)
                .unwrap_or(serde_json::Value::Null),
            final_decision: verdict.decision.clone(),
            final_severity: verdict.severity.clone(),
            final_authority: verdict.authority.clone(),
            capture_quality: quality_status(&checkpoint.capture_quality).to_string(),
            identity_coverage,
            identity_coverage_unavailable_reason,
            action_outcomes: ActionOutcomeCounts::from_actions(&checkpoint.actions),
            attack_attempted: None,
            attack_executed: None,
            attack_succeeded: None,
            reference_status: REFERENCE_STATUS_UNREVIEWED.to_string(),
            primary_eligible: false,
            primary_eligibility_reason: "no independent execution reference or scoring protocol"
                .to_string(),
        }
    }
}

fn usable_system_score(score: f32) -> (Option<f32>, Option<String>) {
    if score.is_finite() && (0.0..=1.0).contains(&score) {
        (Some(score), None)
    } else {
        (
            None,
            Some(format!(
                "authoritative classification score {score} is not a finite value in [0,1]"
            )),
        )
    }
}

fn classification_config_sha256(config: &ClassificationConfig) -> String {
    let serialized = serde_json::to_vec(config).expect("classification config is serializable");
    hex::encode(Sha256::digest(serialized))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::replay::checkpoint::{REPLAY_CONTRACT, implementation_fingerprint};
    use crate::responsibility::types::{
        Action, ActionCandidateType, ActionId, CandidateDerivationMode, GraphSnapshot, ProcessId,
        SignalClass,
    };

    fn checkpoint() -> AnalysisCheckpoint {
        AnalysisCheckpoint {
            run_id: "run-metrics-test".into(),
            contract: REPLAY_CONTRACT.into(),
            implementation: implementation_fingerprint(),
            graph: GraphSnapshot::default(),
            identity_input: None,
            identity_policy: None,
            identities: vec![],
            evidence: vec![],
            intents: vec![],
            actions: vec![],
            delegations: vec![],
            score_signals: Default::default(),
            observations: vec![],
            disable_patterns: true,
            classification_config: Default::default(),
            workspace_prefix: String::new(),
            fallback_agent_id: None,
            timeline: Default::default(),
            capture_quality: serde_json::json!({
                "status": "healthy",
                "sources": {"Process:1": {"initial_snapshot_complete": true}},
                "analysis": {"event_counts": {"process": 1}}
            }),
            observer_enabled: None,
            graph_refinements: vec![],
            identity_refinements: vec![],
            evidence_refinements: vec![],
            non_link_count: 0,
            captured_request_contexts: None,
            graph_inputs: None,
        }
    }

    fn classification(score: f32) -> ClassificationResult {
        ClassificationResult {
            score,
            route: "direct".into(),
            ..ClassificationResult::default()
        }
    }

    fn verdict(decision: AlertDecision) -> FinalVerdict {
        FinalVerdict {
            decision,
            severity: AlertSeverity::Critical,
            authority: AlertAuthority::Authoritative,
            reasoning: "test verdict".into(),
            provenance: vec![],
        }
    }

    fn action(outcome: ActionOutcome, syscall: Option<&str>) -> Action {
        Action {
            outcome,
            syscall_ret: syscall.map(|_| 0),
            syscall_name: syscall.map(str::to_string),
            action_id: ActionId::new(),
            process_id: ProcessId::new(1, 1),
            action_type: ActionCandidateType::NetworkConnect,
            normalized_args: vec![],
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: 1,
        }
    }

    #[test]
    fn system_score_is_actual_triage_even_when_verdict_is_unsafe() {
        let metrics = RunMetrics::build(
            &checkpoint(),
            &classification(0.22),
            &verdict(AlertDecision::Unsafe),
        );
        assert_eq!(metrics.system_score, Some(0.22));
        assert_eq!(
            serde_json::to_value(&metrics).unwrap()["schema"],
            "run-metrics/1"
        );
        assert_eq!(metrics.final_decision, AlertDecision::Unsafe);
        assert_eq!(
            metrics.score_source,
            "authoritative_classification_triage/1"
        );
        assert!(!metrics.score_is_probability);
        assert_eq!(metrics.calibrated_probability, None);
    }

    #[test]
    fn non_finite_and_out_of_range_scores_are_unavailable() {
        for score in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY, -0.01, 1.01] {
            let metrics = RunMetrics::build(
                &checkpoint(),
                &classification(score),
                &verdict(AlertDecision::Safe),
            );
            assert_eq!(
                metrics.system_score, None,
                "score {score} must not be coerced"
            );
            assert!(
                metrics
                    .system_score_unavailable_reason
                    .as_deref()
                    .is_some_and(|reason| reason.contains("not a finite value in [0,1]")),
                "score {score} needs an explicit unavailable reason"
            );
            let json = serde_json::to_value(&metrics).unwrap();
            assert!(json["system_score"].is_null());
        }
    }

    #[test]
    fn degraded_capture_keeps_observation_and_quality_flag() {
        let mut checkpoint = checkpoint();
        checkpoint.capture_quality = serde_json::json!({
            "status": "degraded",
            "sources": {}
        });
        let metrics = RunMetrics::build(
            &checkpoint,
            &classification(0.5),
            &verdict(AlertDecision::Unsafe),
        );
        assert_eq!(metrics.system_score, Some(0.5));
        assert_eq!(metrics.capture_quality, "degraded");
    }

    #[test]
    fn missing_identity_input_makes_coverage_unavailable_without_zero_fill() {
        let metrics = RunMetrics::build(
            &checkpoint(),
            &classification(0.1),
            &verdict(AlertDecision::Safe),
        );
        assert!(metrics.identity_coverage.is_none());
        assert!(
            metrics
                .identity_coverage_unavailable_reason
                .as_deref()
                .is_some_and(|reason| reason.contains("identity view unavailable"))
        );
        let json = serde_json::to_value(&metrics).unwrap();
        assert!(json["identity_coverage"].is_null());
    }

    #[test]
    fn unknown_actions_are_counted_not_deleted() {
        let mut checkpoint = checkpoint();
        checkpoint.actions = vec![
            action(ActionOutcome::Unknown, None),
            action(ActionOutcome::Attempted, Some("connect")),
        ];
        let metrics = RunMetrics::build(
            &checkpoint,
            &classification(0.3),
            &verdict(AlertDecision::Uncertain),
        );
        assert_eq!(metrics.action_outcomes.unknown, 1);
        assert_eq!(metrics.action_outcomes.attempted, 1);
        assert_eq!(metrics.action_outcomes.total, 2);
        assert_eq!(metrics.attack_attempted, None);
    }

    #[test]
    fn attack_flags_stay_unreviewed_even_with_syscalls() {
        let mut checkpoint = checkpoint();
        checkpoint.actions = vec![action(ActionOutcome::Succeeded, Some("write"))];
        let metrics = RunMetrics::build(
            &checkpoint,
            &classification(0.95),
            &verdict(AlertDecision::Unsafe),
        );
        assert_eq!(metrics.attack_attempted, None);
        assert_eq!(metrics.attack_executed, None);
        assert_eq!(metrics.attack_succeeded, None);
        assert_eq!(metrics.reference_status, "unreviewed");
        assert!(!metrics.primary_eligible);
    }

    #[test]
    fn provenance_hashes_the_full_classification_config() {
        let mut checkpoint = checkpoint();
        checkpoint.classification_config.threshold_unsafe = 0.42;
        let expected = hex::encode(Sha256::digest(
            serde_json::to_vec(&checkpoint.classification_config).unwrap(),
        ));
        let metrics = RunMetrics::build(
            &checkpoint,
            &classification(0.4),
            &verdict(AlertDecision::Uncertain),
        );
        assert_eq!(
            metrics.implementation_fingerprint,
            implementation_fingerprint()
        );
        assert_eq!(metrics.classification_config_sha256, expected);
        assert_eq!(metrics.schema_version, RUN_METRICS_VERSION);
    }
}
