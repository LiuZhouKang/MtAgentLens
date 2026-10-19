// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

pub mod cache;
pub mod llm;
pub mod mock;
pub mod provider;
pub mod system_prompt;
pub mod types;

// ── Provider re-exports ──────────────────────────────────────────────────
pub use mock::MockObserverProvider;
pub use provider::{
    ObserverLLMProvider, ObserverProvider, ObserverProviderConfig, ObserverProviderError,
};
pub use system_prompt::OBSERVER_SYSTEM_PROMPT;

// ── CANONICAL type re-exports (§16) ─────────────────────────────────────
// All observer-related types live in types.rs. classification/types.rs
// re-exports from here for backward compatibility.
pub use types::{
    AlertAuthority, CacheKey, CachePrior, CacheTTL, ClassificationConfig, ClassificationResult,
    ContentSignals, CorrelationSignal, DisplayProfile, EvidenceSignature, GraphSignature,
    ObserverDecision, ObserverError, ObserverInput, ObserverOutput, ObserverSignature,
    PatternSignal, PatternVerdict, ResourceSignal, RoleSignal, RoleSignal as ObserverRoleSignal,
    Route, RuleHitSignal, RuleVerdict,
};
