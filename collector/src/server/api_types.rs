// SPDX-License-Identifier: Apache-2.0 OR MIT

use crate::framework::analyzers::pipeline_contract::StageMetrics;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Health status for the pipeline and individual stages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum HealthStatus {
    Healthy,
    Degraded,
    Unhealthy,
}

/// Schema version for the health response payload.
pub const HEALTH_SCHEMA_VERSION: &str = "v1.0.0";

/// Full health response returned by GET /health.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthResponse {
    pub schema_version: String,
    pub status: HealthStatus,
    pub uptime_seconds: u64,
    pub version: String,
    pub degraded_stages: Option<Vec<String>>,
    pub warnings: Option<Vec<String>>,
    pub stages: HashMap<String, StageMetricsSnapshot>,
}

/// Per-stage metrics snapshot exposed in the health response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StageMetricsSnapshot {
    pub events_processed: u64,
    pub events_emitted: u64,
    pub errors: u64,
    pub avg_latency_us: u64,
    pub p99_latency_us: u64,
    pub buffer_size: usize,
    pub status: String,
}

impl StageMetricsSnapshot {
    pub fn from_stage_metrics(m: &StageMetrics) -> Self {
        Self {
            events_processed: m.events_processed,
            events_emitted: m.events_emitted,
            errors: m.errors,
            avg_latency_us: m.avg_latency_us,
            p99_latency_us: 0,
            buffer_size: m.buffer_size,
            status: m.status.as_str().to_string(),
        }
    }
}
