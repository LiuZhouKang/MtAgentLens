use std::collections::HashMap;
use crate::responsibility::observer::types::{
    CacheKey, CachePrior, CacheTTL, ObserverDecision, ObserverOutput,
};

// ── L2 Record ───────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct L2Record {
    pub output: ObserverOutput,
    pub count: u32,
}

// ── ObserverCache (L1 + L2, in-memory only) ─────────────────────────

/// Two-level cache for observer decisions.
///
/// **L1** — Exact hash dedup (`HashMap<u32, ObserverOutput>`).
/// A hit means we have already evaluated an identical signature and can
/// return the cached result immediately.
///
/// **L2** — Resource-pattern anomaly rate (`HashMap<u32, Vec<L2Record>>`).
/// Tracks aggregate safe/unsafe ratios per resource pattern. Survives
/// `clear_task_bound()` so fleet-level anomaly rates persist across tasks
/// within a single run.
pub struct ObserverCache {
    /// L1: exact signature → output (cleared per task boundary).
    l1: HashMap<u32, ObserverOutput>,
    /// L2: signature key → list of observed outcomes with counts.
    l2: HashMap<u32, Vec<L2Record>>,
}

impl ObserverCache {
    pub fn new() -> Self {
        Self {
            l1: HashMap::new(),
            l2: HashMap::new(),
        }
    }

    // ── L1 operations ───────────────────────────────────────────────

    /// Look up an exact signature in L1.
    /// Returns `None` on miss (caller should proceed to evaluate, then store).
    pub fn lookup(&self, key: CacheKey) -> Option<ObserverOutput> {
        self.l1.get(&key.0).cloned()
    }

    // ── Store ───────────────────────────────────────────────────────

    /// Store an observer result.
    ///
    /// Always writes to L1. Writes to L2 when `ttl` is `Task` or `All`
    /// (i.e. the result is useful for fleet-level pattern tracking).
    pub fn store(&mut self, key: CacheKey, output: ObserverOutput, ttl: CacheTTL) {
        self.l1.insert(key.0, output.clone());

        if ttl != CacheTTL::Once {
            let records = self.l2.entry(key.0).or_default();
            if let Some(existing) = records.iter_mut().find(|r| {
                r.output.decision == output.decision
            }) {
                existing.count += 1;
            } else {
                records.push(L2Record { output, count: 1 });
            }
            // Keep at most 5 distinct outcomes per key.
            if records.len() > 5 {
                records.sort_by_key(|r| -(r.count as i32));
                records.truncate(5);
            }
        }
    }

    // ── L2 anomaly rate ─────────────────────────────────────────────

    /// Fraction of L2 records for `key` that were not `Safe`.
    pub fn l2_anomaly_rate(&self, key: CacheKey) -> f32 {
        match self.l2.get(&key.0) {
            Some(records) if !records.is_empty() => {
                let total: u32 = records.iter().map(|r| r.count).sum();
                let anomalous: u32 = records
                    .iter()
                    .filter(|r| r.output.decision != ObserverDecision::Safe)
                    .map(|r| r.count)
                    .sum();
                anomalous as f32 / total.max(1) as f32
            }
            _ => 0.0,
        }
    }

    /// Fleet-level anomaly rate for a resource pattern (L2-based, in-memory).
    /// In the current in-memory-only design this delegates to [`l2_anomaly_rate`].
    pub fn fleet_anomaly_rate(&self, key: CacheKey) -> f32 {
        self.l2_anomaly_rate(key)
    }

    // ── Prior ───────────────────────────────────────────────────────

    /// Build a [`CachePrior`] for the given key from L2 state.
    /// This is attached to [`ObserverInput`] before calling the LLM so the
    /// provider can weight the decision against historical anomaly rates.
    pub fn build_prior(&self, key: CacheKey) -> CachePrior {
        let records = self.l2.get(&key.0);
        let l2_hit = records.is_some_and(|r| !r.is_empty());
        let l2_anomaly_rate = if l2_hit {
            self.l2_anomaly_rate(key)
        } else {
            0.0
        };
        CachePrior {
            l2_hit,
            l2_prior_anomaly_rate: Some(l2_anomaly_rate),
            l3_hit: false,
            l3_fleet_anomaly_rate: None,
        }
    }

    // ── Task boundary ───────────────────────────────────────────────

    /// Clear L1 (exact dedup). L2 persists so fleet anomaly rates carry
    /// across tasks within the same run.
    pub fn clear_task_bound(&mut self) {
        self.l1.clear();
    }
}
