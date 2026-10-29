// SPDX-License-Identifier: Apache-2.0 OR MIT
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

pub mod auth_header_remover;
// causal_link_* modules removed — replaced by ResponsibilityRuntime
pub mod common;
pub mod connection_buffer;
pub mod event;
pub mod file_access_audit; // FAAP-001: Compilation fixed, wiring ready
pub mod file_access_audit_contract;
pub mod file_logger;
pub mod handoff_injection;
pub mod http_filter;
pub mod http_parser;
pub mod multi_agent_anomaly;
pub mod output;
pub mod payload_normalizer;
pub mod presets;
pub mod raw_secret_redactor;
pub mod scenario_vocabulary;
pub mod security_signoff;
pub mod sse_processor;
pub mod ssl_filter;
pub mod stage_metrics;
pub mod stdio_secret_redactor;
pub mod timestamp_normalizer;

#[cfg(test)]
mod sse_processor_tests;

pub use auth_header_remover::AuthHeaderRemover;
pub use connection_buffer::ConnectionBuffer;

// CausalLinkStreamingAnalyzer removed — replaced by ResponsibilityRuntime routing
pub use file_access_audit::FileAccessAuditAnalyzer;
pub use file_logger::FileLogger;
pub use handoff_injection::{
    HandoffInjectionAnalyzer, InjectionPattern, default_injection_patterns,
};
pub use http_filter::{HTTPFilter, print_global_http_filter_metrics};
pub use http_parser::HTTPParser;
pub use multi_agent_anomaly::MultiAgentAnomalyAnalyzer;
pub use output::OutputAnalyzer;
pub use raw_secret_redactor::RawSecretRedactor;
pub use scenario_vocabulary::{
    OVERRIDE_ENV as SCENARIO_VOCABULARY_OVERRIDE_ENV, ScenarioVocabulary, SharedArtifactPattern,
};
pub use security_signoff::{SecuritySignoffAnalyzer, SecuritySignoffConfig, SignoffState};
pub use sse_processor::SSEProcessor;
pub use ssl_filter::{SSLFilter, print_global_ssl_filter_metrics};
pub use stdio_secret_redactor::StdioSecretRedactor;
pub use timestamp_normalizer::TimestampNormalizer;
