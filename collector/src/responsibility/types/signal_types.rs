use serde::{Deserialize, Serialize};

use super::NormalizedResource;
use super::ids::{AgentId, BoundaryId, EdgeId, PolicyId};

// ═══ Detection confidence constants ═══

/// Default confidence for regex-based signal detection.
pub const DETECTION_CONFIDENCE_REGEX: f64 = 0.80;
/// Default confidence for heuristic-based signal detection.
pub const DETECTION_CONFIDENCE_HEURISTIC: f64 = 0.65;
/// Default confidence for ONNX-model-based signal detection.
pub const DETECTION_CONFIDENCE_ONNX: f64 = 0.75;
/// Default confidence for taint-propagation-based signal detection.
pub const DETECTION_CONFIDENCE_TAINT: f64 = 0.70;
/// Default confidence for delegation-based signal detection.
pub const DETECTION_CONFIDENCE_DELEGATION: f64 = 0.85;
/// Default confidence for policy-evaluation-based signal detection.
pub const DETECTION_CONFIDENCE_POLICY: f64 = 0.80;

// ═══ Time window constants ═══

/// Joint upgrade window in nanoseconds (5 minutes).
/// Two signals within this window may trigger joint escalation.
pub const JOINT_UPGRADE_WINDOW_NS: u64 = 300_000_000_000;

/// Signal expire window in nanoseconds (15 minutes).
/// Signals older than this are considered stale.
pub const SIGNAL_EXPIRE_WINDOW_NS: u64 = 900_000_000_000;

// ═══ Joint upgrade rules ═══

/// Pairs of SignalType that, when detected together within
/// `JOINT_UPGRADE_WINDOW_NS`, trigger a joint tier escalation.
pub const JOINT_UPGRADE_RULES: &[(SignalType, SignalType)] = &[
    (SignalType::ContentInjection, SignalType::ResourceAccess),
    (SignalType::Delegation, SignalType::ContentInjection),
    (SignalType::Delegation, SignalType::ResourceAccess),
    (SignalType::ContentInjection, SignalType::TaintPropagation),
];

// ═══ Signal type classification ═══

/// The semantic category of a detected signal.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub enum SignalType {
    /// Content injection detected (e.g. prompt injection, tool response manipulation).
    ContentInjection,
    /// Resource access anomaly detected (e.g. unexpected file/network access).
    ResourceAccess,
    /// Delegation pattern detected (e.g. agent handing off to another agent).
    Delegation,
    /// Taint propagation detected (e.g. untrusted input flowing to sensitive sink).
    TaintPropagation,
    /// Suspicious exec arguments detected.
    ExecArgs,
}

// ═══ Detection method ═══

/// How the signal was detected — captures both the method and its parameters.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum DetectionMethod {
    /// Regex-based pattern matching with the pattern string.
    Regex(String),
    /// Heuristic scoring with a numeric score and human-readable factors.
    Heuristic { score: f64, factors: Vec<String> },
    /// ONNX model inference with model name and confidence score.
    Onnx { model: String, score: f32 },
    /// Policy rule evaluation.
    PolicyEvaluation,
    /// Taint tracking check.
    TaintCheck,
    /// Delegation pattern check.
    DelegationCheck,
}

// ═══ Signal tier ═══

/// Escalation tier for a detected signal.
/// L1 is the lowest severity, L3 the highest.
/// Derived Ord respects variant declaration order: L1 < L2 < L3.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum SignalTier {
    /// Lowest tier — carries an explanation for why it was assigned.
    L1 { reason: String },
    /// Medium tier.
    L2,
    /// Highest tier — demands immediate attention.
    L3,
}

// ═══ Escalation step ═══

/// A single step in a signal's escalation chain.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EscalationStep {
    /// The tier before escalation.
    pub from_tier: SignalTier,
    /// The tier after escalation.
    pub to_tier: SignalTier,
    /// Human-readable reason for the escalation.
    pub reason: String,
}

// ═══ Detected signal ═══

/// A single detected signal produced by the detection pipeline.
#[derive(PartialEq, Debug, Clone, Serialize, Deserialize)]
pub struct DetectedSignal {
    /// The semantic type of the signal.
    pub signal_type: SignalType,
    /// The detection method used to discover this signal.
    pub method: DetectionMethod,
    /// Confidence score [0.0, 1.0].
    pub detection_confidence: f64,
    /// Detection timestamp in nanoseconds (eBPF ktime).
    pub timestamp_ns: u64,
    /// Raw text snippet that triggered the signal, if available.
    pub raw_snippet: Option<String>,
    /// The policy that this signal relates to, if any.
    pub policy_id: Option<PolicyId>,
    /// The boundary where the signal was detected, if any.
    pub boundary_id: Option<BoundaryId>,
    /// Human-readable explanation of why this signal was raised.
    pub reason: String,
    /// Edges in the responsibility graph that correlate with this signal.
    pub correlated_edges: Vec<EdgeId>,
}

// ═══ Signal cluster ═══

/// A cluster of related signals grouped around a primary graph edge.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalCluster {
    /// The primary edge this cluster is anchored to.
    pub primary_edge_id: EdgeId,
    /// All signals belonging to this cluster.
    pub signals: Vec<DetectedSignal>,
    /// The resolved tier after considering all signals and joint upgrades.
    pub tier: SignalTier,
    /// Aggregate confidence across all signals in the cluster.
    pub aggregate_confidence: f64,
    /// The chain of escalation steps that led to the final tier.
    pub escalation_chain: Vec<EscalationStep>,
    /// Policy ID from the accumulated signals (for claim building).
    pub policy_id: Option<PolicyId>,
    /// Boundary ID from the accumulated signals (for claim building).
    pub boundary_id: Option<BoundaryId>,
}

// ═══ Cross-Cycle Accumulator Types ═══

/// Key for grouping PBC signals across trigger cycles.
/// Signals with the same key accumulate evidence together via Noisy-OR fusion.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct AccumKey {
    /// The agent that crossed the policy boundary.
    pub agent_id: AgentId,
    /// The signal type category (ContentInjection, ResourceAccess, etc.).
    pub signal_type: SignalType,
    /// The policy the boundary crossing relates to.
    pub policy_id: Option<PolicyId>,
    /// The boundary the signal was detected at.
    pub boundary_id: Option<BoundaryId>,
    /// Coarse resource digest for disambiguation (e.g. "file:/etc/passwd", "tool:Bash").
    pub resource_digest: Option<String>,
}

impl AccumKey {
    /// Build from a DetectedSignal, an agent, and the edge's target resource.
    pub fn from_signal(
        agent_id: AgentId,
        signal: &DetectedSignal,
        resource: Option<&NormalizedResource>,
    ) -> Self {
        let resource_digest = resource.map(resource_to_digest);
        Self {
            agent_id,
            signal_type: signal.signal_type.clone(),
            policy_id: signal.policy_id.clone(),
            boundary_id: signal.boundary_id.clone(),
            resource_digest,
        }
    }
}

/// A single weighted evidence entry in the accumulator bucket.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccumEntry {
    /// Edge ID for traceability.
    pub edge_id: EdgeId,
    /// Noisy-OR causal weight w_i in [0, 1].
    pub causal_weight: f64,
    /// Detection confidence of the signal s_i in [0, 1].
    pub detection_confidence: f64,
    /// Timestamp in nanoseconds for temporal decay.
    pub timestamp_ns: u64,
}

/// A bucket of cross-cycle signal evidence for one AccumKey.
/// Uses Noisy-OR fusion with exponential temporal decay.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccumBucket {
    /// Accumulator key this bucket belongs to.
    pub key: AccumKey,
    /// All evidence entries accumulated across cycles.
    pub entries: Vec<AccumEntry>,
    /// The highest individual tier observed (for claim type derivation).
    pub max_tier: SignalTier,
    /// Whether a claim has been emitted from this bucket.
    pub claim_emitted: bool,
    /// The last claim emission timestamp (ns), for hysteresis.
    pub last_emission_ns: u64,
    /// Timestamp of the oldest entry (for GC).
    pub oldest_entry_ns: u64,
}

impl AccumBucket {
    /// Create a new bucket with a single entry.
    pub fn new(key: AccumKey, entry: AccumEntry, tier: SignalTier, _now_ns: u64) -> Self {
        let oldest = entry.timestamp_ns;
        Self {
            key,
            entries: vec![entry],
            max_tier: tier,
            claim_emitted: false,
            last_emission_ns: 0,
            oldest_entry_ns: oldest,
        }
    }

    /// Add a new entry to the bucket.
    pub fn push(&mut self, entry: AccumEntry, tier: SignalTier) {
        self.oldest_entry_ns = self.oldest_entry_ns.min(entry.timestamp_ns);
        if tier > self.max_tier {
            self.max_tier = tier;
        }
        self.entries.push(entry);
        // Cap entries to prevent unbounded growth (keep 80 most recent).
        if self.entries.len() > 100 {
            self.entries.sort_by_key(|e| e.timestamp_ns);
            self.entries.drain(0..self.entries.len() - 80);
        }
    }

    /// Logistic evidence rescaling: S(t) = S_raw / (1 + S_raw)
    /// where S_raw = sum_i(w_i * s_i * exp(-lambda * dt_i))
    pub fn logistic_evidence_score(&self, now_ns: u64) -> f64 {
        const HALF_LIFE_NS: f64 = 300_000_000_000.0;
        const LAMBDA: f64 = std::f64::consts::LN_2 / HALF_LIFE_NS;
        let mut evidence_sum = 0.0f64;
        for entry in &self.entries {
            let dt = (now_ns.saturating_sub(entry.timestamp_ns)) as f64;
            let decay = (-LAMBDA * dt).exp();
            evidence_sum += entry.causal_weight * entry.detection_confidence * decay;
        }
        evidence_sum / (1.0 + evidence_sum)
    }

    /// Noisy-OR fusion (kept for reference).
    pub fn noisy_or_score(&self, now_ns: u64) -> f64 {
        // Half-life in nanoseconds: 5 minutes = 300_000_000_000 ns
        const HALF_LIFE_NS: f64 = 300_000_000_000.0;
        const LAMBDA: f64 = std::f64::consts::LN_2 / HALF_LIFE_NS;

        let mut product = 1.0f64;
        let epsilon = 1e-12;
        for entry in &self.entries {
            let dt = (now_ns.saturating_sub(entry.timestamp_ns)) as f64;
            let decay = (-LAMBDA * dt).exp();
            let causal_power = entry.causal_weight * entry.detection_confidence * decay;
            // Clamp to avoid numerical issues near 1.0.
            let complement = (1.0 - causal_power).max(epsilon);
            product *= complement;
        }
        (1.0 - product).max(0.0).min(1.0)
    }
}

/// Compute a compact resource digest for accumulator key disambiguation.
pub fn resource_to_digest(resource: &NormalizedResource) -> String {
    match resource {
        NormalizedResource::File { canonical_path } => format!("file:{}", canonical_path),
        NormalizedResource::Network { host, port, .. } => format!("net:{}:{}", host, port),
        NormalizedResource::Process { .. } => "process:exec".into(),
        NormalizedResource::LlmConversation { provider, .. } => format!("llm:{}", provider),
        NormalizedResource::ToolCall { tool_name, .. } => format!("tool:{}", tool_name),
        NormalizedResource::Credential { .. } => "credential:access".into(),
        NormalizedResource::Context { .. } => "context:inheritance".into(),
    }
}

// ═══ Match layer ═══

/// Which layer of the multi-layer detection pipeline matched the signal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum MatchLayer {
    /// Layer 1: fast regex/pattern matching.
    Layer1Pattern { category: String, pattern: String },
    /// Layer 2: heuristic scoring.
    Layer2Heuristic { score: f64, factors: Vec<String> },
    /// Layer 3: ONNX model inference.
    Layer3Onnx { model: String, score: f64 },
    /// Layer 4: out-of-distribution detection fallback.
    Layer4Ood { score: f64 },
}
