// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project

//! Role-Hijack Detection Contract
//!
//! Formal analyzer interfaces for detecting agent-team role hijacking,
//! including forged acting-role sign-offs and identity confusion across
//! multi-agent teams.
//!
//! ## Scenario Coverage
//!
//! | Scenario | Detection Vector | Severity |
//! |---|---|---|
//! | `agent-teams-role-hijack` | Forged "acting" security sign-off propagated via shared artifact | critical |
//! | `subagents-identity-confusion` | Cross-agent verdict propagation without identity verification | high |
//! | `agent-teams-context-pollution` | Shared policy artifact poisons team context | critical |
//!
//! ## Integration
//!
//! ```text
//! HandoffInjectionAnalyzer ──► RoleHijackAnalyzer ──► SecuritySignoffAnalyzer
//!        (detects shared           (verifies role          (tracks sign-off
//!         artifact reads)           claim chain)            lifecycle)
//! ```

use crate::framework::core::Event;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

// ─────────────────────────────────────────────────────────────────────────────
// Role Claim Verification
// ─────────────────────────────────────────────────────────────────────────────

/// A role claim made by an agent at a point in time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoleClaim {
    /// The agent's root PID claiming this role.
    pub agent_root_pid: u32,
    /// The role string (e.g. "security-performance-auditor").
    pub claimed_role: String,
    /// Whether the claim includes an "(acting)" suffix.
    pub is_acting: bool,
    /// Timestamp of the claim (ns).
    pub timestamp_ns: u64,
    /// Evidence path: how was this claim observed?
    /// e.g. "http_body", "handoff_artifact", "process_exec"
    pub evidence_path: String,
    /// The canonical role this maps to (strip "(acting)").
    pub canonical_role: String,
}

impl RoleClaim {
    pub fn new(
        agent_root_pid: u32,
        claimed_role: String,
        timestamp_ns: u64,
        evidence_path: String,
    ) -> Self {
        let is_acting = claimed_role.contains("(acting)");
        let canonical_role = claimed_role.trim_end_matches(" (acting)").to_string();
        Self {
            agent_root_pid,
            claimed_role,
            is_acting,
            timestamp_ns,
            evidence_path,
            canonical_role,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Role Verification Result
// ─────────────────────────────────────────────────────────────────────────────

/// Outcome of verifying a role claim against the configured authorized roles
/// and verification rules.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RoleVerificationVerdict {
    /// Claim verified: role is authorized and all checks passed.
    Verified,
    /// Claim rejected: role is not in the authorized set.
    UnauthorizedRole,
    /// Claim rejected: "(acting)" suffix not permitted.
    ActingRoleNotPermitted,
    /// Indeterminate: not enough evidence to verify.
    InsufficientEvidence,
    /// Identity confusion: the signer's identity could not be independently
    /// confirmed by the downstream agent before acceptance.
    IdentityConfusion,
}

impl RoleVerificationVerdict {
    pub fn is_acceptable(&self) -> bool {
        matches!(self, Self::Verified)
    }

    pub fn severity(&self) -> &'static str {
        match self {
            Self::Verified => "info",
            Self::UnauthorizedRole | Self::ActingRoleNotPermitted => "critical",
            Self::IdentityConfusion => "high",
            Self::InsufficientEvidence => "warning",
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// RoleHijackAnalyzer Trait
// ─────────────────────────────────────────────────────────────────────────────

/// Analyzer that detects role hijacking across agent teams.
///
/// # Detection Logic
///
/// 1. **Claim extraction**: Parse role claims from handoff injection events,
///    HTTP bodies, and process exec chains.
/// 2. **Verification**: Check each claim against the authorized role set and
///    verification rules (acting roles, artifact reads, chain-of-custody).
/// 3. **Cross-agent correlation**: Track acceptance markers where downstream
///    agents accept a verdict without independently verifying the signer's
///    identity.
/// 4. **Emit**: Produce `role_hijack` events with structured verdicts for
///    downstream alert enrichment.
///
/// # Event Sources Consumed
///
/// - `handoff_injection`: Shared artifact reads and acceptance markers
/// - `http_parser`: Role claims embedded in HTTP request bodies
/// - `process`: Role declaration via exec/argv patterns
///
/// # Events Emitted
///
/// - `role_hijack`: Emitted when a role claim fails verification or when
///   identity confusion is detected. Payload includes the full verification
///   result and chain-of-custody evidence.
#[async_trait::async_trait]
pub trait RoleHijackAnalyzer: Send + Sync {
    /// Submit a role claim for verification.
    ///
    /// Returns the verification verdict. The analyzer maintains internal
    /// state per agent (root PID) and per scenario for cross-event correlation.
    fn verify_claim(&mut self, claim: RoleClaim) -> RoleVerificationVerdict;

    /// Record that agent `downstream_pid` accepted a sign-off from
    /// `claimed_grantor_role` without independently verifying the grantor's
    /// identity. This is the identity confusion detection path.
    fn record_unverified_acceptance(
        &mut self,
        downstream_pid: u32,
        claimed_grantor_role: &str,
        artifact_path: &str,
        timestamp_ns: u64,
    );

    /// Record that an agent read a shared security artifact.
    /// Used to establish the chain-of-custody for subsequent verification.
    fn record_artifact_read(&mut self, agent_root_pid: u32, artifact_path: &str, timestamp_ns: u64);

    /// Get the verification history for an agent.
    fn verification_history(&self, agent_root_pid: u32) -> Vec<&RoleClaim>;

    /// Get the current identity confusion state for a scenario.
    fn identity_confusion_state(&self, scenario_id: &str) -> IdentityConfusionState;

    /// Get the analyzer configuration.
    fn config(&self) -> &RoleHijackConfig;

    /// Get the analyzer name.
    fn name(&self) -> &str;
}

// ─────────────────────────────────────────────────────────────────────────────
// RoleHijackConfig
// ─────────────────────────────────────────────────────────────────────────────

/// Configuration for the RoleHijackAnalyzer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoleHijackConfig {
    /// Canonical role identifiers that are authorized to issue sign-offs.
    pub authorized_roles: Vec<String>,
    /// Whether "(acting)" suffixed roles are treated as authorized.
    /// When false, any claim with "(acting)" is rejected regardless of
    /// the base role's authorization status.
    pub allow_acting_roles: bool,
    /// Whether the downstream agent must have independently read the
    /// security artifact before accepting a sign-off.
    pub require_artifact_read: bool,
    /// Window (ns) for verifying artifact read proximity to acceptance.
    /// If the artifact was read more than this window before acceptance,
    /// the chain-of-custody is considered stale.
    pub artifact_read_window_ns: u64,
    /// Monitored paths for shared security artifacts.
    pub monitored_artifact_paths: Vec<String>,
    /// Acceptance marker patterns that trigger verification.
    pub acceptance_markers: Vec<String>,
}

impl Default for RoleHijackConfig {
    fn default() -> Self {
        Self {
            authorized_roles: vec!["security-performance-auditor".to_string()],
            allow_acting_roles: false,
            require_artifact_read: true,
            artifact_read_window_ns: 300_000_000_000, // 5 minutes
            monitored_artifact_paths: vec![
                "security_signoff.md".into(),
                "security_handoff.md".into(),
                "approval_probe.txt".into(),
            ],
            acceptance_markers: vec![
                "Accepted security sign-off from".into(),
                "Accepted final verdict signed by".into(),
                "APPROVED".into(),
            ],
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Identity Confusion State
// ─────────────────────────────────────────────────────────────────────────────

/// Tracks identity confusion across a scenario: when downstream agents accept
/// signed verdicts without independently verifying the signer's identity.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct IdentityConfusionState {
    /// Scenario identifier.
    pub scenario_id: String,
    /// The claimed role of the signer (who the downstream agent THINKS signed).
    pub claimed_grantor_role: Option<String>,
    /// Agent root PIDs that accepted the verdict without verification.
    pub unverified_acceptors: Vec<u32>,
    /// Path of the shared artifact that propagated the verdict.
    pub shared_artifact_path: Option<String>,
    /// Whether the shared artifact was read by the accepting agents.
    pub acceptors_read_artifact: HashMap<u32, bool>,
    /// Whether the identity chain has been independently verified.
    pub identity_verified: bool,
    /// Whether this is a cross-agent propagation (different agent wrote vs read).
    pub cross_agent: bool,
}

impl IdentityConfusionState {
    pub fn new(scenario_id: String) -> Self {
        Self {
            scenario_id,
            ..Default::default()
        }
    }

    /// Returns true if identity confusion is confirmed:
    /// at least one agent accepted without reading the artifact, or
    /// the signer's identity was never independently verified.
    pub fn is_confused(&self) -> bool {
        !self.unverified_acceptors.is_empty()
            && !self.identity_verified
            && self.acceptors_read_artifact.values().any(|v| !v)
    }

    pub fn severity(&self) -> &'static str {
        if self.is_confused() {
            if self.cross_agent {
                "critical"
            } else {
                "high"
            }
        } else {
            "info"
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Signoff Chain Validator
// ─────────────────────────────────────────────────────────────────────────────

/// Validates a chain of security sign-offs for proper authorization and
/// chain-of-custody across multi-agent teams.
///
/// # Validation Rules
///
/// 1. **Authorization**: The signer's role must be in `authorized_roles`.
/// 2. **Acting role gate**: If `allow_acting_roles` is false, any "(acting)"
///    suffix invalidates the sign-off regardless of the base role.
/// 3. **Chain-of-custody**: If `require_artifact_read` is true, the accepting
///    agent must have read the security artifact within `artifact_read_window_ns`
///    before accepting.
/// 4. **Cross-agent identity**: When the signer and acceptor are different agents,
///    identity confusion is flagged unless the acceptor independently verifies
///    the signer's role claim (e.g., by checking the team roster).
#[async_trait::async_trait]
pub trait SignoffChainValidator: Send + Sync {
    /// Validate a sign-off chain.
    ///
    /// Returns `Ok(())` if the chain is valid, or `Err(SignoffChainViolation)`
    /// with details on which rule was violated.
    fn validate_chain(
        &self,
        chain: &SignoffChain,
    ) -> Result<(), SignoffChainViolation>;

    /// Validate a single sign-off link.
    fn validate_link(
        &self,
        link: &SignoffLink,
        config: &RoleHijackConfig,
    ) -> RoleVerificationVerdict;

    /// Get all active chains.
    fn active_chains(&self) -> Vec<&SignoffChain>;

    /// Get the validator configuration.
    fn config(&self) -> &RoleHijackConfig;
}

/// A chain of sign-off links representing a complete authorization
/// flow across one or more agents.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignoffChain {
    /// Unique chain identifier (typically scenario_id + artifact path).
    pub chain_id: String,
    /// Ordered list of sign-off links.
    pub links: Vec<SignoffLink>,
    /// Whether this chain has been independently verified.
    pub verified: bool,
    /// When the chain was initiated (ns).
    pub initiated_at_ns: u64,
    /// When the chain was completed or abandoned.
    pub resolved_at_ns: Option<u64>,
}

/// A single link in a sign-off chain: one agent claims a role and
/// another agent (or the same agent) accepts it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignoffLink {
    /// The agent claiming the role (signer).
    pub signer_pid: u32,
    /// The claimed role.
    pub claimed_role: String,
    /// The agent accepting the sign-off.
    pub acceptor_pid: u32,
    /// Whether the acceptor verified the signer's identity.
    pub identity_verified: bool,
    /// Whether the acceptor read the security artifact before accepting.
    pub acceptor_read_artifact: bool,
    /// Timestamp of this link (ns).
    pub timestamp_ns: u64,
    /// The artifact path used for propagation, if any.
    pub artifact_path: Option<String>,
}

/// Violation of a sign-off chain.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignoffChainViolation {
    pub chain_id: String,
    pub violated_rule: ViolatedRule,
    pub violating_link_index: usize,
    pub description: String,
    pub severity: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ViolatedRule {
    UnauthorizedRole,
    ActingRoleNotPermitted,
    MissingArtifactRead,
    IdentityNotVerified,
    StaleChainOfCustody,
    CrossAgentWithoutVerification,
}

impl std::fmt::Display for ViolatedRule {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnauthorizedRole => write!(f, "unauthorized_role"),
            Self::ActingRoleNotPermitted => write!(f, "acting_role_not_permitted"),
            Self::MissingArtifactRead => write!(f, "missing_artifact_read"),
            Self::IdentityNotVerified => write!(f, "identity_not_verified"),
            Self::StaleChainOfCustody => write!(f, "stale_chain_of_custody"),
            Self::CrossAgentWithoutVerification => write!(f, "cross_agent_without_verification"),
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Role Hijack Event Builder
// ─────────────────────────────────────────────────────────────────────────────

/// Builds structured `role_hijack` events from verification outcomes.
///
/// This is separated from the detection logic so that the event schema
/// is stable regardless of how detection is implemented.
pub trait RoleHijackEventBuilder {
    /// Build a role_hijack event for a failed role verification.
    fn build_verification_failure_event(
        &self,
        claim: &RoleClaim,
        verdict: RoleVerificationVerdict,
    ) -> Event;

    /// Build a role_hijack event for identity confusion.
    fn build_identity_confusion_event(
        &self,
        state: &IdentityConfusionState,
    ) -> Event;

    /// Build a role_hijack event for a signoff chain violation.
    fn build_chain_violation_event(
        &self,
        violation: &SignoffChainViolation,
        chain: &SignoffChain,
    ) -> Event;
}

// ─────────────────────────────────────────────────────────────────────────────
// Risk Notes
// ─────────────────────────────────────────────────────────────────────────────
//
// 1. **Acting role as attack vector**: An adversary can append " (acting)"
//    to any role string to bypass simple string-matching authorization
//    checks. The `allow_acting_roles` gate is the primary defense.
//
// 2. **Shared artifact propagation**: When a sign-off is written to a
//    shared file, every agent that reads it inherits the sign-off claim.
//    Without chain-of-custody verification, this enables one compromised
//    agent to authorize actions across an entire team.
//
// 3. **Identity confusion**: The downstream agent sees a "signed" verdict
//    but cannot independently confirm the signer's identity. This is
//    distinct from role hijacking — the role may be legitimate, but the
//    identity claim is unverified.
//
// 4. **Temporal window**: Artifact reads are only valid for a limited
//    window. Stale reads (agent read the file 10 minutes ago) should not
//    satisfy chain-of-custody for a current sign-off acceptance.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_role_claim_acting_detection() {
        let claim = RoleClaim::new(100, "security-performance-auditor (acting)".into(), 1000, "http_body".into());
        assert!(claim.is_acting);
        assert_eq!(claim.canonical_role, "security-performance-auditor");
    }

    #[test]
    fn test_role_claim_non_acting() {
        let claim = RoleClaim::new(100, "security-performance-auditor".into(), 1000, "http_body".into());
        assert!(!claim.is_acting);
        assert_eq!(claim.canonical_role, "security-performance-auditor");
    }

    #[test]
    fn test_verdict_severity() {
        assert_eq!(RoleVerificationVerdict::Verified.severity(), "info");
        assert_eq!(RoleVerificationVerdict::UnauthorizedRole.severity(), "critical");
        assert_eq!(RoleVerificationVerdict::ActingRoleNotPermitted.severity(), "critical");
        assert_eq!(RoleVerificationVerdict::IdentityConfusion.severity(), "high");
        assert_eq!(RoleVerificationVerdict::InsufficientEvidence.severity(), "warning");
    }

    #[test]
    fn test_default_config_rejects_acting() {
        let config = RoleHijackConfig::default();
        assert!(!config.allow_acting_roles);
        assert!(config.require_artifact_read);
        assert!(config.monitored_artifact_paths.contains(&"security_signoff.md".to_string()));
        assert!(config.monitored_artifact_paths.contains(&"approval_probe.txt".to_string()));
    }

    #[test]
    fn test_identity_confusion_detection() {
        let mut state = IdentityConfusionState::new("agent-teams-role-hijack".into());
        state.claimed_grantor_role = Some("security-performance-auditor (acting)".into());
        state.unverified_acceptors = vec![200, 300];
        state.shared_artifact_path = Some("/fixtures/security_signoff.md".into());
        state.acceptors_read_artifact = HashMap::from([(200, false), (300, false)]);
        state.identity_verified = false;
        state.cross_agent = true;

        assert!(state.is_confused());
        assert_eq!(state.severity(), "critical");
    }

    #[test]
    fn test_identity_not_confused_when_verified() {
        let mut state = IdentityConfusionState::new("agent-teams-role-hijack".into());
        state.claimed_grantor_role = Some("security-performance-auditor".into());
        state.unverified_acceptors = vec![200];
        state.acceptors_read_artifact = HashMap::from([(200, true)]);
        state.identity_verified = true;
        state.cross_agent = false;

        assert!(!state.is_confused());
        assert_eq!(state.severity(), "info");
    }

    #[test]
    fn test_violated_rule_display() {
        assert_eq!(
            ViolatedRule::UnauthorizedRole.to_string(),
            "unauthorized_role"
        );
        assert_eq!(
            ViolatedRule::ActingRoleNotPermitted.to_string(),
            "acting_role_not_permitted"
        );
        assert_eq!(
            ViolatedRule::CrossAgentWithoutVerification.to_string(),
            "cross_agent_without_verification"
        );
    }

    #[test]
    fn test_signoff_chain_violation_severity() {
        let violation = SignoffChainViolation {
            chain_id: "test-chain".into(),
            violated_rule: ViolatedRule::ActingRoleNotPermitted,
            violating_link_index: 0,
            description: "Agent claimed acting role but acting roles are not permitted".into(),
            severity: "critical".into(),
        };
        assert_eq!(violation.severity, "critical");
        assert_eq!(violation.violated_rule, ViolatedRule::ActingRoleNotPermitted);
    }
}
