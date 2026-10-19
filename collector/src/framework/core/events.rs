// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use super::timestamp::epoch_ns_to_epoch_ms;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// Core event structure for the observability framework
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Event {
    pub timestamp: u64,
    pub source: String,
    pub pid: u32,
    pub comm: String,
    pub data: serde_json::Value,
}

impl Event {
    /// Create a new event with current timestamp in nanoseconds since UNIX epoch.
    #[allow(dead_code)]
    pub fn new(source: String, pid: u32, comm: String, data: serde_json::Value) -> Self {
        Self {
            timestamp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos() as u64,
            source,
            pid,
            comm,
            data,
        }
    }

    /// Create a new event with custom timestamp
    pub fn new_with_timestamp(
        timestamp: u64,
        source: String,
        pid: u32,
        comm: String,
        data: serde_json::Value,
    ) -> Self {
        Self {
            timestamp,
            source,
            pid,
            comm,
            data,
        }
    }

    /// Get the event timestamp as a DateTime<Utc>.
    pub fn datetime(&self) -> DateTime<Utc> {
        let secs = (self.timestamp / 1_000_000_000) as i64;
        let nanos = (self.timestamp % 1_000_000_000) as u32;
        DateTime::from_timestamp(secs, nanos).unwrap_or_else(Utc::now)
    }

    /// Convert to a presentation-friendly JSON value.
    ///
    /// The serialized `timestamp` remains milliseconds since UNIX epoch for
    /// frontend compatibility, while `timestamp_ns` preserves the canonical
    /// internal nanosecond timestamp.
    pub fn to_json_value(&self) -> Value {
        let mut obj = Map::new();
        obj.insert(
            "timestamp".to_string(),
            Value::from(epoch_ns_to_epoch_ms(self.timestamp)),
        );
        obj.insert("timestamp_ns".to_string(), Value::from(self.timestamp));
        obj.insert("source".to_string(), Value::String(self.source.clone()));
        obj.insert("pid".to_string(), Value::from(self.pid));
        obj.insert("comm".to_string(), Value::String(self.comm.clone()));
        obj.insert("data".to_string(), self.data.clone());
        Value::Object(obj)
    }

    /// Convert to JSON string.
    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string(&self.to_json_value())
    }

    /// Serialize this event to pretty-printed JSON.
    #[allow(dead_code)]
    pub fn to_json_pretty(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string_pretty(&self.to_json_value())
    }

    /// Deserialize an event from JSON string.
    #[allow(dead_code)]
    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }
}

impl std::fmt::Display for Event {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "[{}] {} ({}:{}): {}",
            self.datetime().format("%Y-%m-%d %H:%M:%S%.3f"),
            self.source,
            self.comm,
            self.pid,
            self.data
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_event_creation() {
        let data = json!({"key": "value", "number": 42});
        let event = Event::new(
            "test-source".to_string(),
            1234,
            "test-comm".to_string(),
            data.clone(),
        );

        assert!(event.timestamp > 0);
        assert_eq!(event.source, "test-source");
        assert_eq!(event.pid, 1234);
        assert_eq!(event.comm, "test-comm");
        assert_eq!(event.data, data);
    }

    #[test]
    fn test_event_with_custom_timestamp() {
        let data = json!({"test": true});
        let custom_timestamp = 1234567890u64;

        let event = Event::new_with_timestamp(
            custom_timestamp,
            "custom-source".to_string(),
            5678,
            "custom-comm".to_string(),
            data.clone(),
        );

        assert_eq!(event.timestamp, custom_timestamp);
        assert_eq!(event.source, "custom-source");
        assert_eq!(event.pid, 5678);
        assert_eq!(event.comm, "custom-comm");
        assert_eq!(event.data, data);
    }

    #[test]
    fn test_event_json_serialization() {
        let data = json!({"message": "hello world"});
        let event = Event::new_with_timestamp(
            1000,
            "test".to_string(),
            9999,
            "test-comm".to_string(),
            data,
        );

        let json_str = event.to_json().unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();

        assert_eq!(parsed.get("timestamp").and_then(|v| v.as_u64()), Some(0));
        assert_eq!(
            parsed.get("timestamp_ns").and_then(|v| v.as_u64()),
            Some(1000)
        );
        assert_eq!(parsed.get("source").and_then(|v| v.as_str()), Some("test"));
    }

    #[test]
    fn test_event_display() {
        let data = json!({"msg": "test"});
        let event = Event::new_with_timestamp(
            1_609_459_200_000_000_000, // 2021-01-01 00:00:00 UTC in epoch ns
            "test-source".to_string(),
            777,
            "display-comm".to_string(),
            data,
        );

        let display_str = format!("{}", event);
        assert!(display_str.contains("test-source"));
        assert!(display_str.contains("2021"));
        assert!(display_str.contains("display-comm"));
        assert!(display_str.contains("777"));
    }
}
