pub mod provider;
pub mod mock;
pub mod system_prompt;
pub mod cache;
pub mod llm;
pub mod types;

// ── Provider re-exports ──────────────────────────────────────────────────
pub use provider::{ObserverProvider, ObserverProviderConfig, ObserverLLMProvider, ObserverProviderError};
pub use mock::MockObserverProvider;
pub use system_prompt::OBSERVER_SYSTEM_PROMPT;

// ── CANONICAL type re-exports (§16) ─────────────────────────────────────
// All observer-related types live in types.rs. classification/types.rs
// re-exports from here for backward compatibility.
pub use types::{
    AlertAuthority, CacheKey, CachePrior, CacheTTL, ClassificationConfig,
    ClassificationResult, ContentSignals, CorrelationSignal, DisplayProfile,
    EvidenceSignature, GraphSignature, ObserverDecision, ObserverError,
    ObserverInput, ObserverOutput, ObserverSignature, PatternSignal,
    PatternVerdict, ResourceSignal, RoleSignal, RoleSignal as ObserverRoleSignal,
    Route, RuleHitSignal, RuleVerdict,
};
