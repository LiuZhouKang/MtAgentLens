// SPDX-License-Identifier: Apache-2.0 OR MIT

use std::time::Duration;

/// Measurements taken after the terminal consumer finishes routing an event.
/// These do not stand in for uninstrumented upstream stages or source loss.
#[derive(Debug, Default)]
pub struct TerminalHealth {
    pub events_processed: u64,
    pub events_emitted: u64,
    pub errors: u64,
    pub stopped: bool,
    total_processing_ns: u128,
}

impl TerminalHealth {
    pub fn record_processed(&mut self, elapsed: Duration, emitted: usize, errors: u64) {
        self.events_processed += 1;
        self.events_emitted += emitted as u64;
        self.errors += errors;
        self.total_processing_ns += elapsed.as_nanos();
    }

    pub fn is_ready(&self) -> bool {
        self.events_processed > 0 && !self.stopped
    }

    pub fn snapshot(&self) -> serde_json::Value {
        serde_json::json!({
            "events_processed": self.events_processed,
            "events_emitted": self.events_emitted,
            "errors": self.errors,
            "errors_scope": "partial TLS events observed during terminal routing",
            "stopped": self.stopped,
            "avg_processing_us": if self.events_processed > 0 {
                Some(self.total_processing_ns as f64 / self.events_processed as f64 / 1000.0)
            } else { None },
            "p99_processing_us": serde_json::Value::Null,
            "queue_lag": serde_json::Value::Null,
        })
    }
}
