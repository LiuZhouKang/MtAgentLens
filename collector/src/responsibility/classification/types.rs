// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// classification/types.rs — Re-export facade.
//
// ALL observer types are now canonical in observer/types.rs (§16).
// This file re-exports them for backward compatibility.
// New code should import from crate::responsibility::observer::types.

pub use crate::responsibility::observer::types::{
    AlertAuthority, CacheKey, CachePrior, CacheTTL, ClassificationConfig, ClassificationResult,
    ContentSignals, CorrelationSignal, DisplayProfile, EvidenceSignature, GraphSignature,
    ObserverDecision, ObserverError, ObserverInput, ObserverOutput, ObserverSignature,
    PatternSignal, PatternVerdict, ResourceSignal, RoleSignal, Route, RuleHitSignal, RuleVerdict,
};

// Re-export ScoreBreakdown for consumers that import it from here
pub use crate::responsibility::types::refinement::ScoreBreakdown;
