// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Stage identifiers and health metrics for the `/api/health` endpoint.
//!
//! The detection chain is composed in `main.rs` (per-runner analyzers plus the
//! global ones). This module deliberately does not describe or enforce that
//! composition: it used to carry a second, self-referential "pipeline
//! contract" that no analyzer read, so it could stay green while the real
//! chain changed. The producer-wiring test is the single source of truth.

use serde::{Deserialize, Serialize};

/// Ordered pipeline stages used to label health metrics.
///
/// The order documents the intended flow; `main.rs` owns the actual
/// registration order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, PartialOrd, Ord)]
pub enum PipelineStage {
    /// Normalize timestamps, merge SSE chunks, parse HTTP
    Preprocessing = 0,
    /// Extract intents, classify actions, correlate causal links
    CausalLinking = 1,
    /// Track file reads, detect shared-artifact acceptance
    InjectionDetection = 2,
    /// Detect anomalies (command loops, resource storms, team propagation)
    AnomalyDetection = 3,
    /// Deduplicate alerts, assign severity, enrich with propagation chains
    AlertEnrichment = 4,
    /// Persist to SQLite + JSONL, emit to frontend
    Output = 5,
}

impl PipelineStage {
    /// Stages that must complete before this stage can process.
    pub fn prerequisites(self) -> &'static [PipelineStage] {
        match self {
            Self::Preprocessing => &[],
            Self::CausalLinking => &[Self::Preprocessing],
            Self::InjectionDetection => &[Self::Preprocessing],
            Self::AnomalyDetection => &[Self::Preprocessing],
            Self::AlertEnrichment => &[
                Self::CausalLinking,
                Self::InjectionDetection,
                Self::AnomalyDetection,
            ],
            Self::Output => &[Self::AlertEnrichment],
        }
    }
}

/// Stage-level health status.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum StageStatus {
    #[default]
    Healthy,
    Degraded,
    Error,
}

impl StageStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            StageStatus::Healthy => "healthy",
            StageStatus::Degraded => "degraded",
            StageStatus::Error => "error",
        }
    }
}

/// Per-stage metrics snapshot.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StageMetrics {
    pub events_processed: u64,
    pub events_emitted: u64,
    pub errors: u64,
    pub avg_latency_us: u64,
    pub buffer_size: usize,
    pub status: StageStatus,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stage_prerequisites() {
        assert!(PipelineStage::Preprocessing.prerequisites().is_empty());
        assert_eq!(
            PipelineStage::CausalLinking.prerequisites(),
            &[PipelineStage::Preprocessing]
        );
        assert_eq!(
            PipelineStage::AlertEnrichment.prerequisites(),
            &[
                PipelineStage::CausalLinking,
                PipelineStage::InjectionDetection,
                PipelineStage::AnomalyDetection,
            ]
        );
    }

    #[test]
    fn stage_status_strings_are_stable() {
        assert_eq!(StageStatus::Healthy.as_str(), "healthy");
        assert_eq!(StageStatus::Degraded.as_str(), "degraded");
        assert_eq!(StageStatus::Error.as_str(), "error");
        assert_eq!(StageStatus::default(), StageStatus::Healthy);
    }
}
