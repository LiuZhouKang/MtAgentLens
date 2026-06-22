// SPDX-License-Identifier: MIT
//
// output.rs — Structured output formatting (§18).
//
// Formats responsibility pipeline outputs for:
//   - Console / terminal display (human-readable)
//   - JSON export (machine-readable)
//   - Metrics summary (Prometheus-compatible)
//   - Structured log emission
//
// All output flows through this module so formatting decisions
// are centralized and consistent across the codebase.

use serde::{Deserialize, Serialize};
use crate::responsibility::types::alert_types::ResponsibilityAlert;
use crate::responsibility::types::ids::AgentId;
use crate::responsibility::scoring::progressive::DimIndex;

// ── Output Format ────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OutputFormat {
    /// Human-readable text (default).
    Text,
    /// Machine-readable JSON.
    Json,
    /// Prometheus-compatible metrics exposition format.
    Metrics,
}

// ── Evaluation Summary ───────────────────────────────────────────────────

/// Structured evaluation summary produced at the end of a session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvaluationSummary {
    pub session_id: String,
    pub run_duration_ms: u64,
    pub total_events: u64,
    pub total_intents: u64,
    pub total_actions: u64,
    pub pbc_edge_count: u64,
    pub claim_count: u64,
    pub alert_count: u64,
    pub observer_calls: u64,
    pub classification_route: String,
    pub final_score: f32,
    pub per_agent_scores: Vec<AgentScoreSummary>,
    pub alerts: Vec<AlertSummary>,
}

/// Per-agent score summary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentScoreSummary {
    pub agent_id: String,
    pub fused_score: f64,
    pub dimension_scores: [f64; 6],
    pub cusum_triggered: bool,
    pub evidence_conflict: bool,
    pub baseline_established: bool,
}

/// Condensed alert summary for output.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertSummary {
    pub alert_id: String,
    pub subject: String,
    pub decision: String,
    pub severity: String,
    pub confidence: f32,
    pub title: String,
    pub claims: usize,
}

// ── Scored Output ────────────────────────────────────────────────────────

/// Per-dimension score breakdown for output.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoreOutput {
    pub agent_id: String,
    pub fused: f64,
    pub cal_p: f64, // calibrated probability
    pub d1_boundary: f64,
    pub d2_content: f64,
    pub d3_agentic: f64,
    pub d4_temporal: f64,
    pub d5_cross_context: f64,
    pub d6_behavioral: f64,
    pub risk_level: String,
}

// ── Output Writer ────────────────────────────────────────────────────────

/// Central output formatter.
///
/// Consumes evaluation summaries and emits them in the configured format.
pub struct OutputWriter {
    format: OutputFormat,
}

impl OutputWriter {
    pub fn new(format: OutputFormat) -> Self {
        Self { format }
    }

    /// Format an evaluation summary according to the configured format.
    pub fn format_summary(&self, summary: &EvaluationSummary) -> String {
        match self.format {
            OutputFormat::Text => self.format_text(summary),
            OutputFormat::Json => serde_json::to_string_pretty(summary).unwrap_or_default(),
            OutputFormat::Metrics => self.format_metrics(summary),
        }
    }

    fn format_text(&self, summary: &EvaluationSummary) -> String {
        let mut out = String::new();
        out.push_str(&format!("Session: {}\n", summary.session_id));
        out.push_str(&format!("Duration: {}ms | Events: {} | Intents: {} | Actions: {}\n",
            summary.run_duration_ms, summary.total_events, summary.total_intents, summary.total_actions));
        out.push_str(&format!("Graph: {} PBC edges | {} claims | {} alerts\n",
            summary.pbc_edge_count, summary.claim_count, summary.alert_count));
        out.push_str(&format!("Observer calls: {} | Route: {} | Final score: {:.3}\n",
            summary.observer_calls, summary.classification_route, summary.final_score));

        for agent in &summary.per_agent_scores {
            out.push_str(&format!(
                "  Agent {}: fused={:.3} D1={:.3} D2={:.3} D3={:.3} D4={:.3} D5={:.3} D6={:.3} cusum={} conflict={}\n",
                agent.agent_id, agent.fused_score,
                agent.dimension_scores[0], agent.dimension_scores[1],
                agent.dimension_scores[2], agent.dimension_scores[3],
                agent.dimension_scores[4], agent.dimension_scores[5],
                agent.cusum_triggered, agent.evidence_conflict,
            ));
        }

        for alert in &summary.alerts {
            out.push_str(&format!(
                "  ALERT {}: {} | {} | {} | conf={:.2} | claims={}\n",
                alert.alert_id, alert.subject, alert.decision,
                alert.severity, alert.confidence, alert.claims,
            ));
        }

        out
    }

    fn format_metrics(&self, summary: &EvaluationSummary) -> String {
        let mut out = String::new();
        let prefix = "mtagentprism_responsibility";
        out.push_str(&format!("# HELP {}_events_total Total events processed.\n", prefix));
        out.push_str(&format!("# TYPE {}_events_total counter\n", prefix));
        out.push_str(&format!("{}_events_total {}\n", prefix, summary.total_events));

        out.push_str(&format!("# HELP {}_pbc_edges PolicyBoundaryCrossing edges.\n", prefix));
        out.push_str(&format!("# TYPE {}_pbc_edges gauge\n", prefix));
        out.push_str(&format!("{}_pbc_edges {}\n", prefix, summary.pbc_edge_count));

        out.push_str(&format!("# HELP {}_claims_total Total claims.\n", prefix));
        out.push_str(&format!("# TYPE {}_claims_total counter\n", prefix));
        out.push_str(&format!("{}_claims_total {}\n", prefix, summary.claim_count));

        out.push_str(&format!("# HELP {}_alerts_total Total alerts.\n", prefix));
        out.push_str(&format!("# TYPE {}_alerts_total counter\n", prefix));
        out.push_str(&format!("{}_alerts_total {}\n", prefix, summary.alert_count));

        out.push_str(&format!("# HELP {}_final_score Final classification score.\n", prefix));
        out.push_str(&format!("# TYPE {}_final_score gauge\n", prefix));
        out.push_str(&format!("{}_final_score {:.3}\n", prefix, summary.final_score));

        for agent in &summary.per_agent_scores {
            let labels = format!("agent_id=\"{}\"", agent.agent_id);
            out.push_str(&format!("{}_fused_score{{{}}} {:.3}\n", prefix, labels, agent.fused_score));
        }

        out
    }
}

impl Default for OutputWriter {
    fn default() -> Self {
        Self::new(OutputFormat::Text)
    }
}

// ── Convenience constructors ─────────────────────────────────────────────

impl EvaluationSummary {
    pub fn new(session_id: String) -> Self {
        Self {
            session_id,
            run_duration_ms: 0,
            total_events: 0,
            total_intents: 0,
            total_actions: 0,
            pbc_edge_count: 0,
            claim_count: 0,
            alert_count: 0,
            observer_calls: 0,
            classification_route: String::new(),
            final_score: 0.0,
            per_agent_scores: Vec::new(),
            alerts: Vec::new(),
        }
    }

    /// Build a score output for a specific agent from its progressive scores.
    pub fn build_score_output(
        agent_id: &AgentId,
        fused_score: f64,
        dims: &[f64; 6],
        is_unsafe: bool,
    ) -> ScoreOutput {
        let cal_p = crate::responsibility::scoring::fusion::platt_sigmoid(fused_score);
        let risk_level = if cal_p > 0.82 { "critical" }
            else if cal_p > 0.65 { "high" }
            else if cal_p > 0.45 { "medium" }
            else { "low" };

        ScoreOutput {
            agent_id: agent_id.to_string(),
            fused: fused_score,
            cal_p,
            d1_boundary: dims[0],
            d2_content: dims[1],
            d3_agentic: dims[2],
            d4_temporal: dims[3],
            d5_cross_context: dims[4],
            d6_behavioral: dims[5],
            risk_level: risk_level.to_string(),
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_text_empty() {
        let summary = EvaluationSummary::new("test-1".into());
        let writer = OutputWriter::new(OutputFormat::Text);
        let output = writer.format_summary(&summary);
        assert!(output.contains("Session: test-1"));
        assert!(output.contains("Events: 0"));
    }

    #[test]
    fn test_format_json() {
        let summary = EvaluationSummary::new("test-2".into());
        let writer = OutputWriter::new(OutputFormat::Json);
        let output = writer.format_summary(&summary);
        assert!(output.contains("\"session_id\""));
        assert!(output.contains("test-2"));
    }

    #[test]
    fn test_format_metrics() {
        let summary = EvaluationSummary::new("test-3".into());
        let writer = OutputWriter::new(OutputFormat::Metrics);
        let output = writer.format_summary(&summary);
        assert!(output.contains("mtagentprism_responsibility_events_total"));
        assert!(output.contains("mtagentprism_responsibility_final_score"));
    }

    #[test]
    fn test_score_output_risk_levels() {
        let agent = AgentId::from("a1");
        let dims = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4];
        let critical = EvaluationSummary::build_score_output(&agent, 0.85, &dims, true);
        assert_eq!(critical.risk_level, "critical");

        let low = EvaluationSummary::build_score_output(&agent, 0.1, &dims, false);
        assert_eq!(low.risk_level, "low");
    }
}
