#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResponsibilityTable {
    Runs,
    Observations,
    IdentityBindings,
    EvidenceRecords,
    EvidenceDiagnostics,
    ResponsibilityNodes,
    ResponsibilityEdges,
    PolicyJudgments,
    AccountabilityClaims,
    AccountabilityAlerts,
    AccountabilityTracebacks,
    AuditSummaries,
}

impl ResponsibilityTable {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Runs => "responsibility_runs",
            Self::Observations => "responsibility_observations",
            Self::IdentityBindings => "responsibility_identity_bindings",
            Self::EvidenceRecords => "responsibility_evidence_records",
            Self::EvidenceDiagnostics => "responsibility_evidence_diagnostics",
            Self::ResponsibilityNodes => "responsibility_nodes",
            Self::ResponsibilityEdges => "responsibility_edges",
            Self::PolicyJudgments => "policy_judgments",
            Self::AccountabilityClaims => "responsibility_claims",
            Self::AccountabilityAlerts => "responsibility_alerts",
            Self::AccountabilityTracebacks => "responsibility_tracebacks",
            Self::AuditSummaries => "responsibility_audit_summaries",
        }
    }
}

pub const RESPONSIBILITY_TABLES: [ResponsibilityTable; 12] = [
    ResponsibilityTable::Runs,
    ResponsibilityTable::Observations,
    ResponsibilityTable::IdentityBindings,
    ResponsibilityTable::EvidenceRecords,
    ResponsibilityTable::EvidenceDiagnostics,
    ResponsibilityTable::ResponsibilityNodes,
    ResponsibilityTable::ResponsibilityEdges,
    ResponsibilityTable::PolicyJudgments,
    ResponsibilityTable::AccountabilityClaims,
    ResponsibilityTable::AccountabilityAlerts,
    ResponsibilityTable::AccountabilityTracebacks,
    ResponsibilityTable::AuditSummaries,
];

pub const RESPONSIBILITY_ALERT_JOIN_PATH: [&str; 6] = [
    "responsibility_alerts",
    "responsibility_claims",
    "policy_judgments",
    "responsibility_edges",
    "responsibility_evidence_records",
    "responsibility_observations",
];
