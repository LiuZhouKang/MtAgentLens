// SPDX-License-Identifier: Apache-2.0 OR MIT
// 统一实时 SSE 事件帧：GET /api/live/events 的载荷单元。
//
// 直播（record/trace）中，后端把 ResponsibilityAlert（含图快照）与原始框架
// Event 经广播通道实时送达前端。本模块定义统一流的单帧格式（event_type +
// data + 单调 id），与 responsibility SSE 的帧格式保持一致（id:/event:/data:）。

use crate::framework::core::Event;
use crate::responsibility::types::alert_types::ResponsibilityAlert;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::sync::broadcast;
use tokio_stream::Stream;
use tokio_stream::wrappers::BroadcastStream;

/// 一条统一实时 SSE 事件。
#[derive(Debug, Clone)]
pub struct LiveSseEvent {
    /// SSE 事件类型："alert"（责任告警）| "event"（原始事件）| "lag"（丢弃）| "heartbeat"。
    pub event_type: String,
    /// JSON 序列化载荷。
    pub data: String,
    /// 单调递增事件 id（客户端断线重连用）。
    pub id: Option<String>,
}

impl LiveSseEvent {
    /// 格式化为 SSE 帧（无尾换行，调用方追加双换行分隔符）。
    pub fn to_frame(&self) -> String {
        let mut frame = String::new();
        if let Some(ref id) = self.id {
            frame.push_str(&format!("id: {}\n", id));
        }
        frame.push_str(&format!("event: {}\n", self.event_type));
        // SSE 规范：多行 data 每行加 "data: " 前缀。
        for line in self.data.lines() {
            frame.push_str(&format!("data: {}\n", line));
        }
        frame
    }

    /// 构造告警事件（含 graph_snapshot，前端据此实时点亮态势图）。
    pub fn alert(alert: &ResponsibilityAlert, id: Option<String>) -> Self {
        Self {
            event_type: "alert".into(),
            data: serde_json::to_string(alert).unwrap_or_default(),
            id,
        }
    }

    /// 构造原始框架事件（`timestamp_ns` 保留内部纳秒约定）。
    pub fn raw(event: &Event, id: Option<String>) -> Self {
        Self {
            event_type: "event".into(),
            data: event.to_json_value().to_string(),
            id,
        }
    }

    /// 构造 lag 事件——某广播通道落后，丢弃 N 条（N 来自 `BroadcastStreamRecvError::Lagged(u64)`）。
    pub fn lag(dropped: u64, id: Option<String>) -> Self {
        Self {
            event_type: "lag".into(),
            data: format!(r#"{{"dropped":{dropped}}}"#),
            id,
        }
    }

    /// 心跳事件（空载荷，客户端忽略）。
    pub fn heartbeat() -> Self {
        Self {
            event_type: "heartbeat".into(),
            data: String::new(),
            id: None,
        }
    }
}

/// 构造统一实时 SSE 事件流：原始事件（必有）+ 责任告警（可选）+ 30s 心跳。
///
/// 三条来源经 `futures::stream::select` 合并；事件 id 由**共享 `AtomicU64`** 提供
/// 全局单调序列（跨类型不重复，随 SSE `id` 下发）。客户端记录该值但**不做
/// 断线回填**；实时缺口以服务端 `lag` 事件（携带丢弃数）为准，重连后的历史
/// 事件不在本端点补发。
pub fn live_event_stream(
    event_rx: broadcast::Receiver<Event>,
    alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>,
) -> impl Stream<Item = LiveSseEvent> {
    // 仅导入 tokio_stream::StreamExt：其 map/boxed 覆盖所需；若同时引入
    // futures::StreamExt 会与方法解析产生 E0034 歧义。
    use tokio_stream::StreamExt as _;

    let shared = Arc::new(AtomicU64::new(0));

    let raw_shared = Arc::clone(&shared);
    let raw = BroadcastStream::new(event_rx).map(move |res| {
        let id = Some(format!("{}", raw_shared.fetch_add(1, Ordering::SeqCst) + 1));
        match res {
            Ok(event) => LiveSseEvent::raw(&event, id),
            Err(tokio_stream::wrappers::errors::BroadcastStreamRecvError::Lagged(n)) => {
                LiveSseEvent::lag(n, id)
            }
        }
    });

    let alert_shared = Arc::clone(&shared);
    // BoxBody 要求 Send + Sync（http-body 1.0 的 BoxBody = dyn Body + Send + Sync）。
    let alerts: Pin<Box<dyn Stream<Item = LiveSseEvent> + Send + Sync>> = match alert_rx {
        Some(rx) => Box::pin(BroadcastStream::new(rx).map(move |res| {
            let id = Some(format!(
                "{}",
                alert_shared.fetch_add(1, Ordering::SeqCst) + 1
            ));
            match res {
                Ok(alert) => LiveSseEvent::alert(&alert, id),
                Err(tokio_stream::wrappers::errors::BroadcastStreamRecvError::Lagged(n)) => {
                    LiveSseEvent::lag(n, id)
                }
            }
        })),
        None => Box::pin(futures::stream::empty()),
    };

    let heartbeat = tokio_stream::wrappers::IntervalStream::new(tokio::time::interval(
        std::time::Duration::from_secs(30),
    ))
    .map(|_| LiveSseEvent::heartbeat());

    futures::stream::select(heartbeat, futures::stream::select(raw, alerts))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::alert_types::{
        AgentRef, AlertAuthority, AlertDecision, AlertSeverity, BehaviorTimeline, DisplayProfile,
        EngineSection, ResponsibilityAlert,
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
            actionable: true,
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
                claim_ids: Vec::new(),
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
                    raw_score: 0.0,
                    calibrated_score: 0.0,
                    gate_factor: 1.0,
                    gate_version: "test".into(),
                    platt_a: 0.0,
                    platt_b: 0.0,
                    observer_adjusted: false,
                },
                cusum_triggered: false,
                cusum_dimensions: vec![],
                progressive_fused: 0.0,
                observer_trigger_reason: String::new(),
                score_model_version: "test".into(),
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
    fn test_live_sse_alert_frame() {
        let alert = dummy_alert();
        let e = LiveSseEvent::alert(&alert, Some("3".into()));
        let f = e.to_frame();
        assert!(f.starts_with("id: 3\n"));
        assert!(f.contains("event: alert\n"));
        // alert 序列化为单行紧凑 JSON，字段以逗号延续——断言前缀即可
        assert!(f.contains("data: {\"alert_id\":\"a1\""));
        assert!(f.contains("\"phase\":{\"Provisional\":"));
    }

    #[test]
    fn test_live_sse_raw_event_frame() {
        let ev = Event::new_with_timestamp(
            1_700_000_000_000_000_000,
            "tls_capture".into(),
            8120,
            "claude".into(),
            serde_json::json!({ "len": 1200 }),
        );
        let e = LiveSseEvent::raw(&ev, Some("7".into()));
        let f = e.to_frame();
        assert!(f.contains("event: event\n"));
        assert!(f.contains("\"timestamp_ns\":1700000000000000000"));
        assert!(f.contains("\"source\":\"tls_capture\""));
        assert!(f.contains("\"comm\":\"claude\""));
    }

    #[test]
    fn test_live_sse_lag_frame() {
        let f = LiveSseEvent::lag(3, Some("9".into())).to_frame();
        assert!(f.contains("event: lag\n"));
        assert!(f.contains("{\"dropped\":3}"));
    }

    #[test]
    fn test_live_sse_heartbeat() {
        let f = LiveSseEvent::heartbeat().to_frame();
        assert!(f.contains("event: heartbeat\n"));
        // 心跳无载荷、无 id（纯 keep-alive 事件）
        assert!(!f.contains("id:"));
        assert!(!f.contains("data:"));
    }

    #[tokio::test]
    async fn test_stream_merges_alert_and_raw() {
        use futures::StreamExt as _;

        let (event_tx, event_rx) = broadcast::channel::<Event>(16);
        let (alert_tx, alert_rx) = broadcast::channel::<ResponsibilityAlert>(16);
        alert_tx.send(dummy_alert()).unwrap();
        event_tx
            .send(Event::new_with_timestamp(
                1,
                "proc".into(),
                1,
                "c".into(),
                serde_json::json!({}),
            ))
            .unwrap();
        drop(alert_tx);
        drop(event_tx);

        let stream = live_event_stream(event_rx, Some(alert_rx));
        let events: Vec<LiveSseEvent> = stream.take(2).collect().await;
        let types: Vec<String> = events.iter().map(|e| e.event_type.clone()).collect();
        assert!(
            types.contains(&"alert".to_string()),
            "缺少 alert：{types:?}"
        );
        assert!(
            types.contains(&"event".to_string()),
            "缺少 event：{types:?}"
        );
        // 共享 AtomicU64 保证 id 全局单调、互不重复
        let ids: Vec<String> = events.iter().filter_map(|e| e.id.clone()).collect();
        assert_eq!(ids.len(), 2, "两路事件都应带 id：{events:?}");
        assert_ne!(ids[0], ids[1], "id 必须全局唯一：{ids:?}");
    }

    #[tokio::test]
    async fn test_stream_lag_reports_dropped() {
        use futures::StreamExt as _;

        // 缓冲 1，连续发 3 条 → 前两条必然落后，接收方先收到 lag
        let (event_tx, event_rx) = broadcast::channel::<Event>(1);
        for i in 0..3u64 {
            event_tx
                .send(Event::new_with_timestamp(
                    i,
                    "p".into(),
                    1,
                    "c".into(),
                    serde_json::json!({}),
                ))
                .unwrap();
        }
        drop(event_tx);

        let stream = live_event_stream(event_rx, None);
        let events: Vec<LiveSseEvent> = stream.take(2).collect().await;
        assert!(
            events.iter().any(|e| e.event_type == "lag"),
            "应出现 lag 事件：{events:?}"
        );
    }
}
