pub mod engine;
pub mod pattern_matcher;
pub mod rule_engine;
pub mod rules;
pub mod scoring;
pub mod types;

// ── Core classification API ──────────────────────────────────────────

// Unified ClassificationEngine (spec §11). Both Phase 1 (provisional) and
// Phase 2 (authoritative) classification are handled by this single engine.
pub use engine::ClassificationEngine;
pub use engine::ClassificationEngine as PhaseClassificationEngine; // backward compat

// Rule engine (5 Safe + 18 Unsafe)
pub use rule_engine::RuleEngine;

// Standalone scoring functions
pub use scoring::{
    RouteSelection, build_observer_signature, compute_score, compute_score_breakdown,
    heuristic_decide, route_score,
};

// Types
pub use types::{
    AlertAuthority, CachePrior, CacheTTL, ClassificationConfig, ClassificationResult,
    ContentSignals, CorrelationSignal, DisplayProfile, EvidenceSignature, GraphSignature,
    ObserverDecision, ObserverError, ObserverInput, ObserverOutput, ObserverSignature,
    PatternSignal, PatternVerdict, ResourceSignal, RoleSignal, Route, RuleHitSignal, RuleVerdict,
};

// Rules
pub use rules::{RuleDecision, RuleHit, SafetyRule};

// Pattern matcher
pub use pattern_matcher::{
    GraphPatternMatch, GraphPatternMatcher, GraphPatternTemplate, default_templates,
};

// ObserverLLM now canonical at crate::responsibility::observer::llm::ObserverLLM
// Observer cache now canonical at crate::responsibility::observer::cache::ObserverCache
