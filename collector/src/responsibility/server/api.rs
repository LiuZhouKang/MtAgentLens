// SPDX-License-Identifier: Apache-2.0 OR MIT
// Uniform API response and error formats for all server endpoints.

use serde::Serialize;

/// Wraps any response data with authority metadata and a monotonic timestamp.
///
/// # Fields
/// - `data`: The endpoint-specific payload.
/// - `authority`: `"provisional"` for streaming/Phase-1 data, `"authoritative"`
///   for finalized/Phase-2 data.
/// - `timestamp_ns`: Monotonic nanosecond timestamp at response-generation time
///   (bpf_ktime_get_ns epoch).
#[derive(Debug, Clone, Serialize)]
pub struct ApiResponse<T: Serialize> {
    pub data: T,
    #[serde(rename = "authority")]
    pub authority: Authority,
    pub timestamp_ns: u64,
}

/// API-level authority marker.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Authority {
    Provisional,
    Authoritative,
}

impl From<crate::responsibility::types::PhaseIndicator> for Authority {
    fn from(phase: crate::responsibility::types::PhaseIndicator) -> Self {
        match phase {
            crate::responsibility::types::PhaseIndicator::Authoritative { .. } => {
                Authority::Authoritative
            }
            crate::responsibility::types::PhaseIndicator::Provisional { .. } => {
                Authority::Provisional
            }
        }
    }
}

/// Standard error shape returned when a route cannot produce an `ApiResponse`.
#[derive(Debug, Clone, Serialize)]
pub struct ApiError {
    pub code: String,
    pub message: String,
}

impl ApiError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }

    /// The sentinel error for GET /api/runs/active when no run is live.
    pub fn no_active_run() -> Self {
        Self {
            code: "no_active_run".into(),
            message: "No run is currently active.".into(),
        }
    }

    /// Generic 404 for a missing resource.
    pub fn not_found(what: &str) -> Self {
        Self {
            code: format!("{what}_not_found"),
            message: format!("{what} not found."),
        }
    }

    /// Generic 500 for an internal error (message is not exposed to clients).
    pub fn internal(message: impl Into<String>) -> Self {
        Self {
            code: "internal".into(),
            message: message.into(),
        }
    }
}

impl<T: Serialize> ApiResponse<T> {
    /// Build a response with an explicit authority.
    pub fn new(data: T, authority: Authority) -> Self {
        let timestamp_ns = floor_ns();
        Self {
            data,
            authority,
            timestamp_ns,
        }
    }

    /// Build a provisional (Phase-1) response.
    pub fn provisional(data: T) -> Self {
        Self::new(data, Authority::Provisional)
    }

    /// Build an authoritative (Phase-2) response.
    pub fn authoritative(data: T) -> Self {
        Self::new(data, Authority::Authoritative)
    }
}

// ── Helpers to turn ApiResponse / ApiError into hyper responses ──

impl ApiError {
    /// Convert to a `hyper::Response` with the given status code.
    pub fn into_response(self, status: hyper::StatusCode) -> hyper::Response<hyper::body::Bytes> {
        let body = serde_json::to_vec(&self).unwrap_or_default();
        hyper::Response::builder()
            .status(status)
            .header(hyper::header::CONTENT_TYPE, "application/json")
            .body(hyper::body::Bytes::from(body))
            .unwrap()
    }
}

impl<T: Serialize> ApiResponse<T> {
    /// Convert to `hyper::Response<Bytes>` with a 200 status.
    pub fn into_response(self) -> hyper::Response<hyper::body::Bytes> {
        let body = serde_json::to_vec(&self).unwrap_or_default();
        hyper::Response::builder()
            .status(hyper::StatusCode::OK)
            .header(hyper::header::CONTENT_TYPE, "application/json")
            .body(hyper::body::Bytes::from(body))
            .unwrap()
    }
}

// ── Floor timestamp helper ──
// Lives in types/ but we define an accessor here to avoid circular deps.

impl crate::responsibility::types::phase::PhaseIndicator {
    /// Return the nanosecond floor of this phase indicator (for ordering / display).
    pub fn floor_ns(&self) -> u64 {
        match self {
            Self::Provisional { timestamp_ns } => *timestamp_ns,
            Self::Authoritative { timestamp_ns, .. } => *timestamp_ns,
        }
    }
}

/// Convenience: current wall-clock time as nanoseconds since Unix epoch.
/// Uses `SystemTime::now()` which is available on all platforms.
fn floor_ns() -> u64 {
    use std::time::SystemTime;
    let dur = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    dur.as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_api_error_serialization() {
        let err = ApiError::no_active_run();
        let json = serde_json::to_string(&err).unwrap();
        assert!(json.contains("no_active_run"));
        assert!(json.contains("No run is currently active"));
    }

    #[test]
    fn test_api_response_authority() {
        let resp = ApiResponse::provisional(42u64);
        let json = serde_json::to_string(&resp).unwrap();
        assert!(json.contains("\"authority\":\"provisional\""));
    }

    #[test]
    fn test_authority_from_phase() {
        use crate::responsibility::types::PhaseIndicator;
        let p = PhaseIndicator::Provisional { timestamp_ns: 100 };
        assert!(matches!(Authority::from(p), Authority::Provisional));

        let a = PhaseIndicator::Authoritative {
            timestamp_ns: 200,
            overrides: None,
        };
        assert!(matches!(Authority::from(a), Authority::Authoritative));
    }
}
