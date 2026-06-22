// SPDX-License-Identifier: MIT

use crate::framework::core::Event;
use serde_json::Value;
use std::io::BufRead;

pub fn parse_persisted_trace_event(line: &str) -> Result<Event, String> {
    let value: Value =
        serde_json::from_str(line).map_err(|err| format!("invalid trace json: {err}"))?;

    let timestamp_ns = value
        .get("timestamp_ns")
        .and_then(|value| value.as_u64())
        .ok_or_else(|| "missing or invalid timestamp_ns".to_string())?;
    let source = value
        .get("source")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "missing or invalid source".to_string())?;
    let pid = value
        .get("pid")
        .and_then(|value| value.as_u64())
        .ok_or_else(|| "missing or invalid pid".to_string())?;
    let pid = u32::try_from(pid).map_err(|_| "pid out of range".to_string())?;
    let comm = value
        .get("comm")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "missing or invalid comm".to_string())?;
    let data = value
        .get("data")
        .cloned()
        .ok_or_else(|| "missing data".to_string())?;

    Ok(Event::new_with_timestamp(
        timestamp_ns,
        source.to_string(),
        pid,
        comm.to_string(),
        data,
    ))
}

pub fn should_replay_persisted_event(source: &str) -> bool {
    matches!(source, "ssl" | "process" | "stdio" | "system")
}

pub fn is_derived_persisted_event(source: &str) -> bool {
    !should_replay_persisted_event(source)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplayLoadStats {
    pub total_lines: usize,
    pub parsed_events: usize,
    pub skipped_non_raw: usize,
    pub skipped_derived: usize,
    pub malformed_lines: usize,
}

pub fn load_replayable_trace_events<R: BufRead>(
    reader: R,
) -> Result<(Vec<Event>, ReplayLoadStats), String> {
    let mut events = Vec::new();
    let mut stats = ReplayLoadStats {
        total_lines: 0,
        parsed_events: 0,
        skipped_non_raw: 0,
        skipped_derived: 0,
        malformed_lines: 0,
    };

    for line_result in reader.lines() {
        let line = line_result.map_err(|err| format!("failed to read trace line: {err}"))?;
        stats.total_lines += 1;

        let event = match parse_persisted_trace_event(&line) {
            Ok(event) => event,
            Err(_) => {
                stats.malformed_lines += 1;
                continue;
            }
        };

        if !should_replay_persisted_event(&event.source) {
            stats.skipped_non_raw += 1;
            if is_derived_persisted_event(&event.source) {
                stats.skipped_derived += 1;
            }
            continue;
        }

        stats.parsed_events += 1;
        events.push(event);
    }

    Ok((events, stats))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Cursor;

    #[test]
    fn test_parse_persisted_trace_event_uses_timestamp_ns() {
        let line = json!({
            "timestamp": 1714464000123u64,
            "timestamp_ns": 1714464000123456789u64,
            "source": "ssl",
            "pid": 42,
            "comm": "HTTP Client",
            "data": {"function": "READ/RECV", "payload": "demo"}
        })
        .to_string();

        let event = parse_persisted_trace_event(&line).unwrap();

        assert_eq!(event.timestamp, 1714464000123456789u64);
        assert_eq!(event.source, "ssl");
        assert_eq!(event.pid, 42);
        assert_eq!(event.comm, "HTTP Client");
        assert_eq!(event.data["function"], "READ/RECV");
    }

    #[test]
    fn test_parse_persisted_trace_event_rejects_missing_timestamp_ns() {
        let line = json!({
            "timestamp": 1714464000123u64,
            "source": "ssl",
            "pid": 42,
            "comm": "HTTP Client",
            "data": {"function": "READ/RECV"}
        })
        .to_string();

        let error = parse_persisted_trace_event(&line).unwrap_err();

        assert!(error.contains("timestamp_ns"));
    }

    #[test]
    fn test_should_replay_persisted_event_filters_derived_sources() {
        assert!(should_replay_persisted_event("ssl"));
        assert!(should_replay_persisted_event("process"));
        assert!(!should_replay_persisted_event("sse_processor"));
        assert!(!should_replay_persisted_event("http_parser"));
        assert!(!should_replay_persisted_event("causal_trace"));
        assert!(is_derived_persisted_event("sse_processor"));
    }

    #[test]
    fn test_load_replayable_trace_events_tracks_parsed_skipped_and_malformed_lines() {
        let input = [
            json!({
                "timestamp": 1u64,
                "timestamp_ns": 11u64,
                "source": "ssl",
                "pid": 1,
                "comm": "HTTP Client",
                "data": {"function": "READ/RECV"}
            })
            .to_string(),
            json!({
                "timestamp": 2u64,
                "timestamp_ns": 22u64,
                "source": "sse_processor",
                "pid": 1,
                "comm": "HTTP Client",
                "data": {"message_id": "resp_1"}
            })
            .to_string(),
            "{not-json}".to_string(),
        ]
        .join("\n");

        let (events, stats) = load_replayable_trace_events(Cursor::new(input)).unwrap();

        assert_eq!(events.len(), 1);
        assert_eq!(events[0].timestamp, 11u64);
        assert_eq!(events[0].source, "ssl");
        assert_eq!(stats.total_lines, 3);
        assert_eq!(stats.parsed_events, 1);
        assert_eq!(stats.skipped_non_raw, 1);
        assert_eq!(stats.skipped_derived, 1);
        assert_eq!(stats.malformed_lines, 1);
    }
}
