// SPDX-License-Identifier: MIT
// SSE (Server-Sent Events) real-time stream: GET /api/events.
//
// Subscribes the client to a broadcast channel carrying `ResponsibilityAlert`
// values and formats each as a single-line SSE event.

use crate::responsibility::types::alert_types::ResponsibilityAlert;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;

/// A single SSE event frame.
#[derive(Debug, Clone)]
pub struct SseEvent {
    /// SSE event type (e.g. "alert", "heartbeat").
    pub event_type: String,
    /// JSON-serialized payload.
    pub data: String,
    /// Optional event id.
    pub id: Option<String>,
}

impl SseEvent {
    /// Format this event as an SSE text frame (no trailing newline — caller
    /// appends the double-newline separator).
    pub fn to_frame(&self) -> String {
        let mut frame = String::new();
        if let Some(ref id) = self.id {
            frame.push_str(&format!("id: {}\n", id));
        }
        frame.push_str(&format!("event: {}\n", self.event_type));
        // SSE spec: multi-line data is prefixed with "data: " per line.
        for line in self.data.lines() {
            frame.push_str(&format!("data: {}\n", line));
        }
        frame
    }

    /// Construct a heartbeat event (comment-only — clients should ignore).
    pub fn heartbeat() -> Self {
        Self {
            event_type: "heartbeat".into(),
            data: String::new(),
            id: None,
        }
    }

    /// Construct an alert event from a `ResponsibilityAlert`.
    pub fn alert(alert: &ResponsibilityAlert, id: Option<String>) -> Self {
        let data = serde_json::to_string(alert).unwrap_or_default();
        Self {
            event_type: "alert".into(),
            data,
            id,
        }
    }

    /// Construct a handoff observability event. The `data` should be a JSON
    /// string matching the frontend contract envelope:
    ///   `{"stream_type":"handoff_observability","data":{...}}`
    pub fn handoff(data: String) -> Self {
        Self {
            event_type: "message".into(),
            data,
            id: None,
        }
    }

    /// Construct a delegation tree update event. The `data` should be a JSON
    /// string matching the frontend contract envelope:
    ///   `{"stream_type":"delegation_tree","data":{...}}`
    pub fn delegation_tree(data: String) -> Self {
        Self {
            event_type: "message".into(),
            data,
            id: None,
        }
    }
}

/// Build an SSE event stream from a broadcast Receiver.
///
/// Wraps `tokio_stream::BroadcastStream` with sequencing and formatting.
/// Emits `SseEvent::alert()` for each incoming alert and a diagnostic
/// "lag" event when the consumer falls behind.
pub fn alert_event_stream(
    rx: tokio::sync::broadcast::Receiver<ResponsibilityAlert>,
) -> impl tokio_stream::Stream<Item = SseEvent> {
    let mut seq: u64 = 0;
    BroadcastStream::new(rx).filter_map(move |result| {
        seq += 1;
        let id = Some(format!("{}", seq));
        let event = match result {
            Ok(alert) => SseEvent::alert(&alert, id),
            Err(tokio_stream::wrappers::errors::BroadcastStreamRecvError::Lagged(skipped)) => {
                log::warn!("SSE channel lagged, dropped {skipped} alerts");
                SseEvent {
                    event_type: "lag".into(),
                    data: format!(r#"{{"dropped":{skipped}}}"#),
                    id,
                }
            }
        };
        Some(event)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::alert_types::{
        AgentRef, AlertAuthority, AlertDecision, AlertSeverity, DisplayProfile, EngineSection,
        ResponsibilityAlert, BehaviorTimeline,
    };
    use crate::responsibility::types::graph_types::GraphSnapshot;
    use crate::responsibility::types::ids::AlertId;
    use crate::responsibility::types::phase::PhaseIndicator;
    use crate::responsibility::types::refinement::ScoreBreakdown;

    fn dummy_alert() -> ResponsibilityAlert {
        ResponsibilityAlert {
            alert_id: AlertId::from("a1"),
            timestamp_ns: 1000,
            display_profile: DisplayProfile::UnsafeByEngine,
            decision: AlertDecision::Unsafe,
            severity: AlertSeverity::Warning,
            confidence: 0.85,
            authority: AlertAuthority::Supporting,
            reasoning: "test".into(),
            title: "Test".into(),
            summary: "Summary".into(),
            action_hint: None,
            primary_agent: AgentRef {
                agent_id: crate::responsibility::types::ids::AgentId::from("ag1"),
                claimed_role: None,
            },
            involved_agents: vec![],
            engine: EngineSection {
                rule_verdicts: vec![],
                pattern_verdicts: vec![],
                classification_score: 0.0,
                classification_route: String::new(),
                score_breakdown: ScoreBreakdown {
                    rule_score: 0.0,
                    pattern_score: 0.0,
                    boundary_score: 0.0,
                    evidence_score: 0.0,
                    role_score: 0.0,
                    repetition_score: 0.0,
            behavioral_score: 0.0,
                    total: 0.0,
                },
                cusum_triggered: false,
                cusum_dimensions: vec![],
                progressive_fused: 0.0,
                observer_trigger_reason: String::new(),
                deberta_score: 0.0,
            },
            timeline: BehaviorTimeline {
                steps: vec![],
                total_duration_ms: 0,
            },
            graph_snapshot: GraphSnapshot::default(),
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
            provisional_ref: None,
        }
    }

    #[test]
    fn test_sse_frame_format() {
        let event = SseEvent {
            event_type: "alert".into(),
            data: r#"{"alert_id":"a1"}"#.into(),
            id: Some("1".into()),
        };
        let frame = event.to_frame();
        assert!(frame.starts_with("id: 1\n"));
        assert!(frame.contains("event: alert\n"));
        assert!(frame.contains("data: {\"alert_id\":\"a1\"}\n"));
    }

    #[test]
    fn test_sse_heartbeat() {
        let event = SseEvent::heartbeat();
        let frame = event.to_frame();
        assert!(frame.contains("event: heartbeat\n"));
    }

    #[tokio::test]
    async fn test_stream_yields_alert() {
        let (tx, rx) = tokio::sync::broadcast::channel(16);
        let alert = dummy_alert();
        tx.send(alert).unwrap();
        drop(tx);

        let events: Vec<SseEvent> = alert_event_stream(rx).collect().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event_type, "alert");
    }

    #[tokio::test]
    async fn test_stream_closed_yields_nothing() {
        let (_tx, rx) = tokio::sync::broadcast::channel::<ResponsibilityAlert>(16);
        drop(_tx);

        let events: Vec<SseEvent> = alert_event_stream(rx).collect().await;
        assert!(events.is_empty());
    }
}
