// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use async_trait::async_trait;

use crate::responsibility::observer::provider::{ObserverProvider, ObserverProviderError};
use crate::responsibility::observer::types::{
    CacheKey, CachePrior, CacheTTL, ObserverInput, ObserverOutput,
};
use std::collections::HashMap;

/// A provider that replays an exact-signature cache hit.
///
/// The runtime used to pass `None` on a cache hit, so the engine re-derived a
/// heuristic verdict from the static score and silently discarded the cached
/// Observer decision. That made the cache change the decision it was supposed
/// to avoid recomputing. Replaying the stored verdict keeps the decision
/// identical to the call that populated the cache.
pub struct CachedObserver {
    output: ObserverOutput,
}

impl CachedObserver {
    pub fn new(output: ObserverOutput) -> Self {
        Self { output }
    }
}

#[async_trait]
impl ObserverProvider for CachedObserver {
    async fn evaluate(
        &self,
        _input: &ObserverInput,
    ) -> Result<ObserverOutput, ObserverProviderError> {
        Ok(self.output.clone())
    }
}

// ── ObserverCache (L1 exact-signature dedup only) ───────────────────

/// Exact-signature cache for Observer decisions.
///
/// Only L1 remains: an identical signature returns the previous verdict.
/// The old L2 "anomaly prior" fed earlier Observer verdicts back into later
/// prompts; with a non-deterministic Observer that is a self-confirmation
/// loop, not evidence weighting. It was removed rather than left as a
/// knob that silently biases the arbiter.
pub struct ObserverCache {
    l1: HashMap<u64, ObserverOutput>,
}

impl Default for ObserverCache {
    fn default() -> Self {
        Self::new()
    }
}

impl ObserverCache {
    pub fn new() -> Self {
        Self { l1: HashMap::new() }
    }

    pub fn lookup(&self, key: CacheKey) -> Option<ObserverOutput> {
        self.l1.get(&key.0).cloned()
    }

    /// Store an Observer result under the TTL the model asked for.
    ///
    /// `Once` means "do not reuse this verdict": storing it made the model's
    /// explicit request unobservable and turned a one-off judgment into a
    /// session-wide prior. `Task` and `All` share the session store because a
    /// task boundary is also the session boundary here.
    pub fn store(&mut self, key: CacheKey, output: ObserverOutput, ttl: CacheTTL) {
        if matches!(ttl, CacheTTL::Once) {
            return;
        }
        self.l1.insert(key.0, output);
    }

    /// Prior is intentionally empty: no verdict feedback.
    pub fn build_prior(&self, _key: CacheKey) -> CachePrior {
        CachePrior::default()
    }

    /// Clear the exact-signature cache at a session/task boundary.
    pub fn clear_task_bound(&mut self) {
        self.l1.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::observer::types::{CacheKey, ObserverDecision};
    use crate::responsibility::types::AlertAuthority;

    fn output() -> ObserverOutput {
        ObserverOutput {
            decision: ObserverDecision::Safe,
            severity: crate::responsibility::claims::ClaimSeverity::Info,
            confidence: 0.9,
            cache_ttl: CacheTTL::Task,
            authority: AlertAuthority::Supporting,
            reasoning: "r".into(),
            attacked_flag: false,
            evidence_cited: vec![],
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        }
    }

    #[test]
    fn once_is_not_stored() {
        let mut cache = ObserverCache::new();
        let key = CacheKey(7);
        cache.store(key, output(), CacheTTL::Once);
        assert!(cache.lookup(key).is_none());
    }

    #[test]
    fn task_scoped_verdict_is_replayed() {
        let mut cache = ObserverCache::new();
        let key = CacheKey(7);
        cache.store(key, output(), CacheTTL::Task);
        assert!(cache.lookup(key).is_some());
        cache.clear_task_bound();
        assert!(cache.lookup(key).is_none());
    }
}
