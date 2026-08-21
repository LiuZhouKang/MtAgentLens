// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project

//! Concrete implementation of the `PipelineBuilder` and `PipelineOrchestrator` traits.
//!
//! ## Architecture
//!
//! `PipelineOrchestratorImpl` composes analyzers into a sequential 6-stage pipeline:
//!
//! ```text
//! Input Event
//!     │
//!     ▼
//! Preprocessing (SSE merge, HTTP parse, timestamp normalize)
//!     │
//!     ▼
//! CausalLinking (intent → action correlation)
//!     │
//!     ▼
//! InjectionDetection (file-read tracking, shared-artifact detection)
//!     │
//!     ▼
//! AnomalyDetection (command loops, resource storms, team propagation)
//!     │
//!     ▼
//! AlertEnrichment (correlate, deduplicate, enrich)
//!     │
//!     ▼
//! Output (persist, emit)
//!     │
//!     ▼
//! Output Events (original + derived)
//! ```
//!
//! ## Risk Notes
//!
//! 1. **Sequential processing** per DDR-001: Events pass through all stages in order.
//!    A slow analyzer blocks the entire pipeline. No per-stage timeout in v1.
//!
//! 2. **No error recovery** per R-CPDP-007: If any analyzer returns an error, the
//!    pipeline transitions to `Error` state and stops. Restart requires rebuild.
//!
//! 3. **block_on usage**: The `process_event` method uses `tokio::runtime::Handle::block_on`
//!    internally to call async `Analyzer::process()` from a sync trait method. This is
//!    safe because the stream is immediately ready (from a Vec), and the method runs
//!    within a tokio runtime context.

use super::Analyzer;
use crate::framework::analyzers::pipeline_contract::{
    PipelineBuildError, PipelineBuilder, PipelineOrchestrator, PipelineOrchestratorState,
    PipelineStage, SharedStateStore, StageContract, StageMetrics, StageStatus,
    canonical_pipeline_contracts,
};
use crate::framework::core::Event;
use futures::stream::{self, StreamExt};
use std::collections::HashMap;

// ─────────────────────────────────────────────────────────────────────────────
// Internal: Per-stage analyzer storage
// ─────────────────────────────────────────────────────────────────────────────

/// Holds the analyzers for a single pipeline stage.
#[derive(Default)]
struct StageAnalyzers {
    /// Ordered list of analyzers for this stage.
    analyzers: Vec<Box<dyn Analyzer>>,
    /// Whether this stage has been configured.
    configured: bool,
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline Orchestrator Implementation
// ─────────────────────────────────────────────────────────────────────────────

/// Concrete implementation of the composed analyzer pipeline.
///
/// Owns the full chain of analyzers (one list per stage) and coordinates
/// lifecycle: start → run (event loop) → drain → stop.
pub struct PipelineOrchestratorImpl {
    /// Current lifecycle state.
    state: PipelineOrchestratorState,
    /// Per-stage analyzer lists.
    stages: HashMap<PipelineStage, StageAnalyzers>,
    /// Optional shared state store (wired in Phase 3).
    shared_store: Option<Box<dyn SharedStateStore>>,
    /// Per-stage runtime metrics.
    metrics: HashMap<PipelineStage, StageMetrics>,
    /// Stage contracts for validation.
    contracts: Vec<StageContract>,
}

impl PipelineOrchestratorImpl {
    /// Create a new empty orchestrator (no analyzers configured).
    /// Use `PipelineOrchestratorImpl::builder()` to construct with analyzers.
    pub fn new() -> Self {
        let contracts = canonical_pipeline_contracts();
        let mut metrics = HashMap::new();

        // Initialize metrics for all stages
        for contract in &contracts {
            metrics.insert(
                contract.stage,
                StageMetrics {
                    events_processed: 0,
                    events_emitted: 0,
                    errors: 0,
                    avg_latency_us: 0,
                    buffer_size: 0,
                    status: StageStatus::default(),
                },
            );
        }

        Self {
            state: PipelineOrchestratorState::Initializing,
            stages: HashMap::new(),
            shared_store: None,
            metrics,
            contracts,
        }
    }

    /// Create a new builder for composing the pipeline.
    pub fn builder() -> PipelineBuilderImpl {
        PipelineBuilderImpl::new()
    }

    /// Process events through a single stage (all analyzers in the stage).
    ///
    /// Returns all events produced by the stage (original pass-through + derived).
    fn process_stage(
        &mut self,
        events: Vec<Event>,
        stage: PipelineStage,
    ) -> Result<Vec<Event>, String> {
        let mut current = events;

        // Check if stage is configured
        if !self.stages.contains_key(&stage) {
            // Unconfigured stages are pass-through (no-op)
            return Ok(current);
        }

        let stage_config = self.stages.get_mut(&stage).unwrap();

        // Stage metrics tracking
        let start = std::time::Instant::now();

        // Run through each analyzer in the stage sequentially
        for analyzer in &mut stage_config.analyzers {
            // Create a stream from the current events
            let input_stream: crate::framework::runners::EventStream =
                Box::pin(stream::iter(current));

            // Call the analyzer's async process method via block_on
            // SAFETY: We're running in a tokio context. The stream is immediately ready
            // (pinned from a Vec), so block_on returns instantly without blocking the
            // runtime for any significant duration.
            let result = tokio::runtime::Handle::current().block_on(analyzer.process(input_stream));
            match result {
                Ok(output_stream) => {
                    // Collect the output stream (also immediately ready)
                    current = tokio::runtime::Handle::current()
                        .block_on(output_stream.collect::<Vec<Event>>());
                }
                Err(e) => {
                    // Record error in metrics
                    if let Some(metrics) = self.metrics.get_mut(&stage) {
                        metrics.errors += 1;
                    }
                    return Err(format!(
                        "Analyzer '{}' in stage {:?} failed: {}",
                        analyzer.name(),
                        stage,
                        e
                    ));
                }
            }
        }

        // Update metrics
        let elapsed_us = start.elapsed().as_micros() as u64;
        if let Some(metrics) = self.metrics.get_mut(&stage) {
            // Prefer to use u64 for the incoming count, but current has moved.
            // For now, we use the output count as a proxy.
            metrics.events_emitted += current.len() as u64;
            // Running average: new_avg = (old_avg * n + latency) / (n + 1)
            let n = metrics.events_processed;
            metrics.avg_latency_us = if n == 0 {
                elapsed_us
            } else {
                (metrics.avg_latency_us.saturating_mul(n) + elapsed_us) / (n + 1)
            };
            metrics.events_processed += 1;
        }

        Ok(current)
    }
}

impl Default for PipelineOrchestratorImpl {
    fn default() -> Self {
        Self::new()
    }
}

impl PipelineOrchestrator for PipelineOrchestratorImpl {
    fn start(&mut self) -> Result<(), String> {
        let current = self.state;
        if !current.can_transition_to(PipelineOrchestratorState::Running) {
            return Err(format!(
                "Cannot start from state {:?}. Must be in Initializing state.",
                current
            ));
        }

        // Validate all stages are configured
        let required_stages = [
            PipelineStage::Preprocessing,
            PipelineStage::CausalLinking,
            PipelineStage::InjectionDetection,
            PipelineStage::AnomalyDetection,
            PipelineStage::AlertEnrichment,
            PipelineStage::Output,
        ];

        let mut missing_stages = Vec::new();
        for stage in &required_stages {
            if !self.stages.contains_key(stage) || !self.stages[stage].configured {
                missing_stages.push(*stage);
            }
        }

        if !missing_stages.is_empty() {
            return Err(format!(
                "Cannot start: missing stages {:?}. Build the pipeline first.",
                missing_stages
            ));
        }

        self.state = PipelineOrchestratorState::Running;
        Ok(())
    }

    fn process_event(&mut self, event: Event) -> Result<Vec<Event>, String> {
        if self.state != PipelineOrchestratorState::Running
            && self.state != PipelineOrchestratorState::Paused
        {
            return Err(format!(
                "Cannot process event in state {:?}. Pipeline must be Running or Paused.",
                self.state
            ));
        }

        // If paused, pass through unchanged
        if self.state == PipelineOrchestratorState::Paused {
            return Ok(vec![event]);
        }

        // Process through each stage sequentially
        let stage_order = [
            PipelineStage::Preprocessing,
            PipelineStage::CausalLinking,
            PipelineStage::InjectionDetection,
            PipelineStage::AnomalyDetection,
            PipelineStage::AlertEnrichment,
            PipelineStage::Output,
        ];

        let mut current_events = vec![event];

        for stage in &stage_order {
            match self.process_stage(current_events, *stage) {
                Ok(events) => {
                    current_events = events;
                }
                Err(e) => {
                    // Transition to error state
                    self.state = PipelineOrchestratorState::Error;
                    return Err(e);
                }
            }
        }

        Ok(current_events)
    }

    fn drain(&mut self) -> Result<(), String> {
        let current = self.state;
        if !current.can_transition_to(PipelineOrchestratorState::Draining) {
            return Err(format!(
                "Cannot drain from state {:?}. Must be Running.",
                current
            ));
        }
        self.state = PipelineOrchestratorState::Draining;
        // In v1, drain is a no-op (no internal buffers to flush).
        // Future: flush per-analyzer buffers if any stage maintains internal state.
        self.state = PipelineOrchestratorState::Stopped;
        Ok(())
    }

    fn force_stop(&mut self) -> Result<(), String> {
        self.state = PipelineOrchestratorState::Stopped;
        Ok(())
    }

    fn pause(&mut self) -> Result<(), String> {
        let current = self.state;
        if !current.can_transition_to(PipelineOrchestratorState::Paused) {
            return Err(format!(
                "Cannot pause from state {:?}. Must be Running.",
                current
            ));
        }
        self.state = PipelineOrchestratorState::Paused;
        Ok(())
    }

    fn resume(&mut self) -> Result<(), String> {
        let current = self.state;
        if !current.can_transition_to(PipelineOrchestratorState::Running) {
            return Err(format!(
                "Cannot resume from state {:?}. Must be Paused.",
                current
            ));
        }
        self.state = PipelineOrchestratorState::Running;
        Ok(())
    }

    fn state(&self) -> PipelineOrchestratorState {
        self.state
    }

    fn stage_metrics(&self) -> HashMap<PipelineStage, StageMetrics> {
        self.metrics.clone()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline Builder Implementation
// ─────────────────────────────────────────────────────────────────────────────

/// Concrete builder for composing a `PipelineOrchestratorImpl`.
///
/// # Example
///
/// ```rust,ignore
/// let mut builder = PipelineBuilderImpl::new();
/// builder
///     .add_preprocessor(Box::new(SSEProcessor::new()))
///     .add_preprocessor(Box::new(HTTPParser::new()))
///     .add_causal_link(Box::new(CausalTraceAnalyzer::new()))
///     .add_injection_detector(Box::new(HandoffInjectionAnalyzer::new()))
///     .add_anomaly_detector(Box::new(MultiAgentAnomalyAnalyzer::new()))
///     .add_alert_enricher(Box::new(AlertEnrichmentAnalyzer::new()))
///     .add_output(Box::new(OutputAnalyzer::new()))
///     .wire_shared_state(Box::new(MutexSharedStateStore::new()));
///
/// let orchestrator = builder.build().expect("Pipeline should build successfully");
/// ```
pub struct PipelineBuilderImpl {
    /// Per-stage analyzer storage.
    stages: HashMap<PipelineStage, StageAnalyzers>,
    /// Optional shared state store.
    shared_store: Option<Box<dyn SharedStateStore>>,
}

impl PipelineBuilderImpl {
    pub fn new() -> Self {
        Self {
            stages: HashMap::new(),
            shared_store: None,
        }
    }
}

impl Default for PipelineBuilderImpl {
    fn default() -> Self {
        Self::new()
    }
}

impl PipelineBuilder for PipelineBuilderImpl {
    fn add_preprocessor(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self {
        self.stages
            .entry(PipelineStage::Preprocessing)
            .or_default()
            .analyzers
            .push(analyzer);
        self.stages
            .get_mut(&PipelineStage::Preprocessing)
            .unwrap()
            .configured = true;
        self
    }

    fn add_causal_link(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self {
        self.stages
            .entry(PipelineStage::CausalLinking)
            .or_default()
            .analyzers
            .push(analyzer);
        self.stages
            .get_mut(&PipelineStage::CausalLinking)
            .unwrap()
            .configured = true;
        self
    }

    fn add_injection_detector(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self {
        self.stages
            .entry(PipelineStage::InjectionDetection)
            .or_default()
            .analyzers
            .push(analyzer);
        self.stages
            .get_mut(&PipelineStage::InjectionDetection)
            .unwrap()
            .configured = true;
        self
    }

    fn add_anomaly_detector(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self {
        self.stages
            .entry(PipelineStage::AnomalyDetection)
            .or_default()
            .analyzers
            .push(analyzer);
        self.stages
            .get_mut(&PipelineStage::AnomalyDetection)
            .unwrap()
            .configured = true;
        self
    }

    fn add_alert_enricher(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self {
        self.stages
            .entry(PipelineStage::AlertEnrichment)
            .or_default()
            .analyzers
            .push(analyzer);
        self.stages
            .get_mut(&PipelineStage::AlertEnrichment)
            .unwrap()
            .configured = true;
        self
    }

    fn add_output(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self {
        self.stages
            .entry(PipelineStage::Output)
            .or_default()
            .analyzers
            .push(analyzer);
        self.stages
            .get_mut(&PipelineStage::Output)
            .unwrap()
            .configured = true;
        self
    }

    fn wire_shared_state(&mut self, store: Box<dyn SharedStateStore>) -> &mut Self {
        self.shared_store = Some(store);
        self
    }

    fn build(&mut self) -> Result<Box<dyn PipelineOrchestrator>, PipelineBuildError> {
        let contracts = canonical_pipeline_contracts();
        let mut missing_stages = Vec::new();
        let mut unsatisfied = Vec::new();

        // Check all required stages are present
        let required = [
            PipelineStage::Preprocessing,
            PipelineStage::CausalLinking,
            PipelineStage::InjectionDetection,
            PipelineStage::AnomalyDetection,
            PipelineStage::AlertEnrichment,
            PipelineStage::Output,
        ];

        for stage in &required {
            match self.stages.get(stage) {
                Some(s) if s.configured => {}
                _ => {
                    missing_stages.push(*stage);
                }
            }
        }

        // Validate stage contracts (each stage's output satisfies the next stage's input)
        for i in 1..contracts.len() {
            let upstream = &contracts[i - 1];
            let downstream = &contracts[i];

            if downstream.stage != PipelineStage::Output && !upstream.satisfies(downstream) {
                unsatisfied.push((upstream.stage, downstream.stage));
            }
        }

        if !missing_stages.is_empty() || !unsatisfied.is_empty() {
            return Err(PipelineBuildError {
                reason: "Pipeline validation failed".to_string(),
                missing_stages,
                unsatisfied_contracts: unsatisfied,
            });
        }

        // Build metrics for each stage
        let mut metrics = HashMap::new();
        for contract in &contracts {
            metrics.insert(
                contract.stage,
                StageMetrics {
                    events_processed: 0,
                    events_emitted: 0,
                    errors: 0,
                    avg_latency_us: 0,
                    buffer_size: 0,
                    status: StageStatus::default(),
                },
            );
        }

        let orchestrator = PipelineOrchestratorImpl {
            state: PipelineOrchestratorState::Initializing,
            stages: std::mem::take(&mut self.stages),
            shared_store: self.shared_store.take(),
            metrics,
            contracts,
        };

        Ok(Box::new(orchestrator))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::analyzers::{Analyzer, AnalyzerError};
    use crate::framework::core::Event;
    use async_trait::async_trait;
    use serde_json::json;

    /// A mock analyzer that tags events with its stage name, used for testing
    /// pipeline composition and event flow.
    struct MockStageAnalyzer {
        name: String,
        stage_tag: &'static str,
    }

    impl MockStageAnalyzer {
        fn new(name: &str, stage_tag: &'static str) -> Self {
            Self {
                name: name.to_string(),
                stage_tag,
            }
        }
    }

    #[async_trait]
    impl Analyzer for MockStageAnalyzer {
        async fn process(
            &mut self,
            stream: crate::framework::runners::EventStream,
        ) -> Result<crate::framework::runners::EventStream, AnalyzerError> {
            let tag = self.stage_tag;
            let mapped = stream.map(move |mut event| {
                // Add a tag to the event data to indicate which stage processed it
                event.data.as_object_mut().map(|obj| {
                    obj.insert(format!("processed_by_{}", tag), json!(true));
                });
                event
            });
            Ok(Box::pin(mapped))
        }

        fn name(&self) -> &str {
            &self.name
        }
    }

    /// A mock analyzer that always fails (for testing error handling).
    struct FailingAnalyzer;

    #[async_trait]
    impl Analyzer for FailingAnalyzer {
        async fn process(
            &mut self,
            _stream: crate::framework::runners::EventStream,
        ) -> Result<crate::framework::runners::EventStream, AnalyzerError> {
            Err(Box::new(std::io::Error::new(
                std::io::ErrorKind::Other,
                "mock failure",
            )))
        }

        fn name(&self) -> &str {
            "FailingAnalyzer"
        }
    }
}
