// SPDX-License-Identifier: MIT
// Copyright (c) 2026 eunomia-bpf org.

use crate::framework::runners::EventStream;
use async_trait::async_trait;

/// Type alias for errors that can be sent between threads
pub type AnalyzerError = Box<dyn std::error::Error + Send + Sync>;

/// Base trait for all analyzers that process event streams
#[async_trait]
pub trait Analyzer: Send + Sync {
    /// Process an event stream and return a processed stream
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    /// Get the name of this analyzer
    #[allow(dead_code)]
    fn name(&self) -> &str;
}

pub mod agent_teams_role_hijack;
pub mod alert_enrichment;  // TODO
pub mod analyzer_composition_contract;
pub mod auth_header_remover;
// causal_link_* modules removed — replaced by ResponsibilityRuntime
pub mod common;
pub mod connection_buffer;
pub mod conversation_flow;
// pub mod context_pollution;  // TODO
pub mod event;
pub mod file_access_audit;  // FAAP-001: Compilation fixed, wiring ready
pub mod file_access_audit_contract;
pub mod file_logger;
pub mod handoff_injection;
pub mod http_filter;
pub mod latency;
pub mod http_parser;
pub mod multi_agent_anomaly;
pub mod observability_metrics;
pub mod output;
pub mod pipeline_contract;
pub mod pipeline_orchestrator;
pub mod role_hijack_contract;
pub mod security_signoff;
// pub mod shared_state_store;  // TODO
pub mod sse_processor;
pub mod ssl_filter;
pub mod timestamp_normalizer;

#[cfg(test)]
mod sse_processor_tests;

pub use agent_teams_role_hijack::{
    AgentTeamsRoleHijackAnalyzer, AgentTeamsRoleHijackConfig, TeamRoleVerificationVerdict,
    TeamSignoffState, TeamSignoffTimestamps,
};
pub use auth_header_remover::AuthHeaderRemover;
pub use connection_buffer::ConnectionBuffer;

// pub use alert_enrichment::AlertEnrichmentAnalyzer;
// CausalLinkStreamingAnalyzer removed — replaced by ResponsibilityRuntime routing
pub use conversation_flow::ConversationFlowAnalyzer;
// pub use context_pollution::ContextPollutionAnalyzer;
pub use file_access_audit::FileAccessAuditAnalyzer;
pub use file_logger::FileLogger;
pub use handoff_injection::{
    HandoffInjectionAnalyzer, InjectionPattern, default_injection_patterns,
};
pub use http_filter::{HTTPFilter, print_global_http_filter_metrics};
pub use http_parser::HTTPParser;
pub use latency::LatencyAnalyzer;
pub use multi_agent_anomaly::MultiAgentAnomalyAnalyzer;
pub use observability_metrics::ObservabilityMetricsAnalyzer;
pub use output::OutputAnalyzer;
pub use pipeline_orchestrator::PipelineOrchestratorImpl;
pub use role_hijack_contract::{
    IdentityConfusionState, RoleClaim, RoleHijackAnalyzer, RoleHijackConfig,
    RoleHijackEventBuilder, RoleVerificationVerdict, SignoffChain, SignoffChainValidator,
    SignoffChainViolation, SignoffLink, ViolatedRule,
};
pub use security_signoff::{SecuritySignoffAnalyzer, SecuritySignoffConfig, SignoffState};
// pub use shared_state_store::MutexSharedStateStore;
pub use sse_processor::SSEProcessor;
pub use ssl_filter::{SSLFilter, print_global_ssl_filter_metrics};
pub use timestamp_normalizer::TimestampNormalizer;
