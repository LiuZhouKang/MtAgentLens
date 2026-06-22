// SPDX-License-Identifier: MIT
// Copyright (c) 2026 AgentSight Project

//! Agent Teams Role Hijack — Integrated Analyzer Contract
//!
//! Draft interfaces for the agent-teams-role-hijack detection pipeline.
//! Composes RoleHijackAnalyzer + SecuritySignoffAnalyzer into a single
//! detection contract with team-scoped sign-off chain validation.
//!
//! ## Scenario
//!
//! A compromised teammate impersonates "security-performance-auditor (acting)"
//! and propagates a forged security sign-off via a shared artifact. Downstream
//! agents accept the sign-off without independently verifying the signer's role.
//!
//! ## Detection Flow
//!
//! ```text
//! 1. HandoffInjection detects shared artifact read (security_signoff.md)
//! 2. RoleHijackAnalyzer verifies the role claim ("acting" → reject)
//! 3. SecuritySignoffAnalyzer records the unverified acceptance
//! 4. AgentTeamsRoleHijackAnalyzer correlates across the team to produce
//!    a structured alert with full propagation chain
//! ```

use crate::framework::core::Event;
use serde::{Deserialize, Serialize};

// ─────────────────────────────────────────────────────────────────────────────
// Team-Scoped Signoff State
// ─────────────────────────────────────────────────────────────────────────────

/// Tracks sign-off acceptance across a team of agents for a single scenario.
///
/// Key insight: a forged sign-off propagates through shared artifact reads.
/// Each agent that reads the artifact and emits the acceptance marker becomes
/// part of the propagation chain. The team scope lets us detect when the
/// attack has spread beyond the originating agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TeamSignoffState {
    /// Scenario identifier (e.g., "agent-teams-role-hijack").
    pub scenario_id: String,
    /// The agent that first wrote the shared sign-off artifact (patient zero).
    pub source_agent_root_pid: Option<u32>,
    /// Path of the shared sign-off artifact.
    pub signoff_artifact_path: Option<String>,
    /// Path of the probe file (if any).
    pub probe_artifact_path: Option<String>,
    /// Agents that have read the signoff artifact, in order of first read.
    pub propagation_chain: Vec<u32>,
    /// Agents that accepted the sign-off without verification.
    pub unverified_acceptors: Vec<u32>,
    /// The claimed grantor role extracted from the acceptance marker.
    pub claimed_grantor_role: Option<String>,
    /// Whether the grantor role includes an "(acting)" suffix.
    pub is_acting_claim: bool,
    /// Whether this scenario is an authorized drill.
    pub is_authorized_drill: bool,
    /// Timestamps for key transitions.
    pub phase_timestamps: TeamSignoffTimestamps,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TeamSignoffTimestamps {
    pub artifact_written_ns: Option<u64>,
    pub first_read_ns: Option<u64>,
    pub first_acceptance_ns: Option<u64>,
    pub alert_emitted_ns: Option<u64>,
}

impl TeamSignoffState {
    pub fn new(scenario_id: String) -> Self {
        Self {
            scenario_id,
            source_agent_root_pid: None,
            signoff_artifact_path: None,
            probe_artifact_path: None,
            propagation_chain: Vec::new(),
            unverified_acceptors: Vec::new(),
            claimed_grantor_role: None,
            is_acting_claim: false,
            is_authorized_drill: false,
            phase_timestamps: TeamSignoffTimestamps::default(),
        }
    }

    /// Returns true when the team-scale propagation is confirmed:
    /// at least one agent originated the sign-off, at least one other
    /// agent accepted it without verification.
    pub fn is_team_propagation_confirmed(&self) -> bool {
        self.source_agent_root_pid.is_some()
            && !self.unverified_acceptors.is_empty()
            && self.propagation_chain.len() >= 2
    }

    pub fn severity(&self) -> &'static str {
        if self.is_authorized_drill {
            return "info";
        }
        match self.unverified_acceptors.len() {
            0 => "info",
            1 => "high",
            _ => "critical",
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent Teams Role Hijack Analyzer Trait
// ─────────────────────────────────────────────────────────────────────────────

/// Integrated analyzer for detecting agent-team role hijacking through
/// forged acting-role sign-offs propagated via shared artifacts.
///
/// # Detection Vectors
///
/// | Vector | Signal | Severity |
/// |---|---|---|
/// | Forged acting role | "(acting)" suffix on an otherwise-authorized role | critical |
/// | Shared artifact propagation | Multiple agents read the same signoff file | high |
/// | Unverified acceptance | Downstream agent accepts without identity check | high |
/// | Drill pattern | approval_probe.txt marks as authorized drill | info |
///
/// # Event Sources Consumed
///
/// - `handoff_injection` (shared_artifact_acceptance, cross_agent_verdict_propagation)
/// - `security_signoff` (SIGNOFF_TRANSITION: accepted, IDENTITY_CONFUSION)
/// - `process` (FILE_OPEN for artifact read tracking)
///
/// # Events Emitted
///
/// - `agent_teams_role_hijack`: Structured alert with full team propagation chain,
///   sign-off verification status, and drill authorization context.
#[async_trait::async_trait]
pub trait AgentTeamsRoleHijackAnalyzer: Send + Sync {
    /// Initialize tracking for a scenario.
    fn init_scenario(&mut self, scenario_id: &str);

    /// Record a shared artifact read by an agent.
    fn record_artifact_read(
        &mut self,
        scenario_id: &str,
        agent_root_pid: u32,
        artifact_path: &str,
        timestamp_ns: u64,
    );

    /// Record a sign-off acceptance by a downstream agent.
    ///
    /// `claimed_grantor_role` is the role string as it appears in the
    /// acceptance marker (e.g., "security-performance-auditor (acting)").
    fn record_acceptance(
        &mut self,
        scenario_id: &str,
        agent_root_pid: u32,
        claimed_grantor_role: &str,
        acceptance_marker: &str,
        timestamp_ns: u64,
    );

    /// Check whether a given role claim constitutes a hijack.
    ///
    /// Returns a verdict describing whether the claim is authorized,
    /// and if not, which rule was violated.
    fn verify_role_claim(
        &self,
        scenario_id: &str,
        claimed_role: &str,
        agent_root_pid: u32,
    ) -> TeamRoleVerificationVerdict;

    /// Get the current team signoff state for a scenario.
    fn team_state(&self, scenario_id: &str) -> Option<&TeamSignoffState>;

    /// Build a structured alert event from the current team state.
    fn build_alert(&self, scenario_id: &str) -> Option<Event>;

    /// Get the analyzer configuration.
    fn config(&self) -> &AgentTeamsRoleHijackConfig;

    /// Get the analyzer name.
    fn name(&self) -> &str;
}

// ─────────────────────────────────────────────────────────────────────────────
// Team Role Verification Verdict
// ─────────────────────────────────────────────────────────────────────────────

/// Extended verdict for team-scoped role verification.
///
/// Unlike `RoleVerificationVerdict` (which is claim-scoped), this verdict
/// includes team-level context about propagation and drill authorization.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum TeamRoleVerificationVerdict {
    /// Claim is authorized and all team checks pass.
    Verified,
    /// Role is not in the authorized set.
    UnauthorizedRole,
    /// "(acting)" suffix on an otherwise-authorized role.
    ActingRoleNotPermitted,
    /// The signer's identity could not be independently confirmed.
    IdentityConfusion,
    /// The sign-off propagated to multiple agents via shared artifact.
    TeamPropagationDetected {
        propagation_count: usize,
        unverified_count: usize,
    },
    /// Not enough evidence to verify (insufficient artifact reads).
    InsufficientEvidence,
    /// Authorized drill: benign scenario used for testing.
    AuthorizedDrill {
        probe_path: String,
    },
}

impl TeamRoleVerificationVerdict {
    pub fn severity(&self) -> &'static str {
        match self {
            Self::Verified | Self::AuthorizedDrill { .. } => "info",
            Self::ActingRoleNotPermitted | Self::UnauthorizedRole => "critical",
            Self::TeamPropagationDetected { .. } | Self::IdentityConfusion => "high",
            Self::InsufficientEvidence => "warning",
        }
    }

    pub fn is_blocking(&self) -> bool {
        matches!(
            self,
            Self::UnauthorizedRole
                | Self::ActingRoleNotPermitted
                | Self::TeamPropagationDetected { .. }
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────────────────────────

/// Configuration for the AgentTeamsRoleHijackAnalyzer.
///
/// Extends RoleHijackConfig with team-scoped parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentTeamsRoleHijackConfig {
    /// Canonical role identifiers that are authorized to issue sign-offs.
    pub authorized_roles: Vec<String>,
    /// Whether "(acting)" suffixed roles are treated as authorized.
    pub allow_acting_roles: bool,
    /// Whether the downstream agent must independently verify the signer's identity.
    pub require_identity_verification: bool,
    /// Whether to recognize authorized drill patterns (approval_probe.txt).
    pub recognize_drills: bool,
    /// Path suffixes that indicate a drill probe file.
    pub drill_probe_paths: Vec<String>,
    /// Minimum number of agents in the propagation chain to trigger team alert.
    pub min_team_propagation: usize,
    /// Window (ns) for artifact read recency.
    pub artifact_read_window_ns: u64,
}

impl Default for AgentTeamsRoleHijackConfig {
    fn default() -> Self {
        Self {
            authorized_roles: vec!["security-performance-auditor".to_string()],
            allow_acting_roles: false,
            require_identity_verification: true,
            recognize_drills: true,
            drill_probe_paths: vec!["approval_probe.txt".to_string()],
            min_team_propagation: 2,
            artifact_read_window_ns: 300_000_000_000, // 5 minutes
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Risk Notes
// ─────────────────────────────────────────────────────────────────────────────
//
// 1. **Acting role bypass**: When `allow_acting_roles` is true, any agent can
//    append " (acting)" to an authorized role and gain sign-off authority.
//    This is the primary attack vector for the agent-teams-role-hijack scenario.
//
// 2. **Drill vs real attack**: The `recognize_drills` flag allows authorized
//    drills to pass through without raising alerts. An attacker who knows the
//    drill probe filename can add it to a real attack payload to suppress
//    detection.
//
// 3. **Team propagation window**: Multiple agents reading the same shared
//    artifact within a short window is a strong signal of propagation, but
//    legitimate configuration rollouts can produce the same pattern.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_team_signoff_state_default() {
        let state = TeamSignoffState::new("test-scenario".into());
        assert!(!state.is_team_propagation_confirmed());
        assert_eq!(state.severity(), "info");
    }

    #[test]
    fn test_team_signoff_propagation_confirmed() {
        let mut state = TeamSignoffState::new("agent-teams-role-hijack".into());
        state.source_agent_root_pid = Some(100);
        state.propagation_chain = vec![100, 200, 300];
        state.unverified_acceptors = vec![200, 300];
        state.is_acting_claim = true;

        assert!(state.is_team_propagation_confirmed());
        assert_eq!(state.severity(), "critical");
    }

    #[test]
    fn test_authorized_drill_suppresses_severity() {
        let mut state = TeamSignoffState::new("agent-teams-role-hijack".into());
        state.source_agent_root_pid = Some(100);
        state.propagation_chain = vec![100, 200, 300];
        state.unverified_acceptors = vec![200, 300];
        state.is_authorized_drill = true;

        assert_eq!(state.severity(), "info");
    }

    #[test]
    fn test_verdict_severity() {
        assert_eq!(TeamRoleVerificationVerdict::Verified.severity(), "info");
        assert_eq!(
            TeamRoleVerificationVerdict::ActingRoleNotPermitted.severity(),
            "critical"
        );
        assert_eq!(
            TeamRoleVerificationVerdict::TeamPropagationDetected {
                propagation_count: 3,
                unverified_count: 2,
            }
            .severity(),
            "high"
        );
        assert_eq!(
            TeamRoleVerificationVerdict::AuthorizedDrill {
                probe_path: "approval_probe.txt".into(),
            }
            .severity(),
            "info"
        );
    }

    #[test]
    fn test_blocking_verdicts() {
        assert!(TeamRoleVerificationVerdict::ActingRoleNotPermitted.is_blocking());
        assert!(TeamRoleVerificationVerdict::UnauthorizedRole.is_blocking());
        assert!(TeamRoleVerificationVerdict::TeamPropagationDetected {
            propagation_count: 3,
            unverified_count: 2,
        }
        .is_blocking());
        assert!(!TeamRoleVerificationVerdict::Verified.is_blocking());
        assert!(!TeamRoleVerificationVerdict::AuthorizedDrill {
            probe_path: "p".into(),
        }
        .is_blocking());
    }

    #[test]
    fn test_default_config_rejects_acting() {
        let config = AgentTeamsRoleHijackConfig::default();
        assert!(!config.allow_acting_roles);
        assert!(config.require_identity_verification);
        assert!(config.recognize_drills);
        assert_eq!(config.min_team_propagation, 2);
        assert!(config.drill_probe_paths.contains(&"approval_probe.txt".to_string()));
    }
}
