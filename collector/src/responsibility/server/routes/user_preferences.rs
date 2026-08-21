// SPDX-License-Identifier: MIT
// Route handlers: GET /api/user/preferences and PUT /api/user/preferences.
//
// The `user_preferences` table is a simple key-value store keyed by `user_id`.
// As there is no authentication layer, a single `"default"` user is used.
// Future iterations may extend this to support per-authenticated-user profiles.
//
// ## Endpoints
//   GET /api/user/preferences  →  { theme: "light" | "dark" | "system" }
//   PUT /api/user/preferences  ←  { theme: "light" | "dark" | "system" }
//                              →  { success: true }

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::persistence::schema::open_responsibility_db;
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Response, body::Bytes};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::SystemTime;

/// The default user ID used when no authentication is available.
pub const DEFAULT_USER_ID: &str = "default";

/// Valid theme values accepted by PUT.
const VALID_THEMES: &[&str] = &["light", "dark", "system"];

// ═══════════════════════════════════════════════════════════════════════════
// Request / Response types
// ═══════════════════════════════════════════════════════════════════════════

/// Response shape for GET /api/user/preferences.
#[derive(Debug, Clone, Serialize)]
pub struct UserPreferencesResponse {
    pub theme: String,
}

/// Request body for PUT /api/user/preferences.
#[derive(Debug, Clone, Deserialize)]
pub struct PutPreferencesRequest {
    pub theme: String,
}

/// Response shape for PUT /api/user/preferences.
#[derive(Debug, Clone, Serialize)]
pub struct SuccessResponse {
    pub success: bool,
}

// ═══════════════════════════════════════════════════════════════════════════
// Handlers
// ═══════════════════════════════════════════════════════════════════════════

/// GET /api/user/preferences — return the current theme preference.
///
/// Returns `{ theme: "system" }` by default when the user has no saved
/// preference — the frontend is responsible for resolving "system" against
/// `prefers-color-scheme`.
pub fn get_user_preferences(reader: &Arc<PersistenceReader>, user_id: &str) -> Response<Bytes> {
    match reader.get_user_preferences(user_id) {
        Ok(Some(row)) => {
            let prefs = UserPreferencesResponse { theme: row.theme };
            ApiResponse::authoritative(prefs).into_response()
        }
        Ok(None) => {
            // No row yet → return the default "system" so the frontend
            // always receives a valid response without an extra DB write.
            let prefs = UserPreferencesResponse {
                theme: "system".to_string(),
            };
            ApiResponse::authoritative(prefs).into_response()
        }
        Err(e) => ApiError::internal(format!("Failed to query preferences: {e}"))
            .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR),
    }
}

/// PUT /api/user/preferences — save (upsert) a theme preference.
///
/// Validates `theme` against `["light", "dark", "system"]`. The `db_path`
/// parameter should come from `ServerState`; when `None` the handler returns
/// a 500 error (caller should always provide a valid path).
pub fn set_user_preferences(
    _reader: &Arc<PersistenceReader>,
    user_id: &str,
    db_path: Option<&str>,
    body: Bytes,
) -> Response<Bytes> {
    // ── 1. Parse request body ──────────────────────────────────────────
    let req_body: PutPreferencesRequest = match serde_json::from_slice(&body) {
        Ok(p) => p,
        Err(e) => {
            return ApiError::new(
                "invalid_request",
                format!("Failed to parse request body: {e}"),
            )
            .into_response(hyper::StatusCode::BAD_REQUEST);
        }
    };

    // ── 2. Validate theme ─────────────────────────────────────────────
    if !VALID_THEMES.contains(&req_body.theme.as_str()) {
        return ApiError::new(
            "invalid_theme",
            format!(
                "Invalid theme '{}'. Must be one of: {}",
                req_body.theme,
                VALID_THEMES.join(", "),
            ),
        )
        .into_response(hyper::StatusCode::BAD_REQUEST);
    }

    // ── 3. Open a write connection ────────────────────────────────────
    let db_path = match db_path {
        Some(p) => p,
        None => {
            return ApiError::internal(
                "No database path available — server was started without a \
                 persistence file; preferences cannot be saved.",
            )
            .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    let conn = match open_responsibility_db(db_path) {
        Ok(c) => c,
        Err(e) => {
            return ApiError::internal(format!("Failed to open database for write: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    // ── 4. Upsert preference row ──────────────────────────────────────
    let now = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;

    let result = conn.execute(
        "INSERT INTO user_preferences (user_id, theme, updated_at)
         VALUES (?1, ?2, ?3)
         ON CONFLICT(user_id) DO UPDATE
           SET theme      = excluded.theme,
               updated_at = excluded.updated_at",
        rusqlite::params![user_id, req_body.theme, now as i64],
    );

    // Close the connection (flush WAL) before responding.
    drop(conn);

    match result {
        Ok(_) => ApiResponse::authoritative(SuccessResponse { success: true }).into_response(),
        Err(e) => ApiError::internal(format!("Failed to save preferences: {e}"))
            .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR),
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::persistence::reader::PersistenceReader;
    use crate::responsibility::persistence::schema::open_responsibility_db;
    use tempfile::tempdir;

    /// Helper: create a temporary responsibility DB, insert a run record so
    /// the FK constraint is satisfied, and return the path + reader + writer
    /// connection.
    fn setup_test_env() -> (tempfile::TempDir, Arc<PersistenceReader>, String) {
        let dir = tempdir().unwrap();
        let path = dir.path().join("prefs_test.db");
        let path_str = path.to_string_lossy().into_owned();

        let conn = open_responsibility_db(&path_str).unwrap();
        // Insert a dummy run so that other FK-dependent inserts could work
        // (the user_preferences table has no FK, so this is just for safety).
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('test-run', 0)",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = Arc::new(PersistenceReader::open(&path_str).unwrap());
        (dir, reader, path_str)
    }

    #[test]
    fn test_get_preferences_default() {
        let (_dir, reader, _path) = setup_test_env();
        let resp = get_user_preferences(&reader, DEFAULT_USER_ID);
        assert_eq!(resp.status(), hyper::StatusCode::OK);

        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert_eq!(body["data"]["theme"], "system");
        assert_eq!(body["authority"], "authoritative");
    }

    #[test]
    fn test_set_and_get_preferences() {
        let (_dir, reader, path) = setup_test_env();

        // Set theme to dark
        let req_body = serde_json::json!({"theme": "dark"});
        let body_bytes = Bytes::from(serde_json::to_vec(&req_body).unwrap());
        let resp = set_user_preferences(&reader, DEFAULT_USER_ID, Some(&path), body_bytes);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert_eq!(body["data"]["success"], true);

        // Read it back
        let resp = get_user_preferences(&reader, DEFAULT_USER_ID);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert_eq!(body["data"]["theme"], "dark");
    }

    #[test]
    fn test_set_invalid_theme_returns_400() {
        let (_dir, reader, path) = setup_test_env();
        let req_body = serde_json::json!({"theme": "neon"});
        let body_bytes = Bytes::from(serde_json::to_vec(&req_body).unwrap());
        let resp = set_user_preferences(&reader, DEFAULT_USER_ID, Some(&path), body_bytes);
        assert_eq!(resp.status(), hyper::StatusCode::BAD_REQUEST);
        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert_eq!(body["code"], "invalid_theme");
    }

    #[test]
    fn test_set_preferences_without_db_path_returns_500() {
        let (_dir, reader, _path) = setup_test_env();
        let req_body = serde_json::json!({"theme": "light"});
        let body_bytes = Bytes::from(serde_json::to_vec(&req_body).unwrap());
        let resp = set_user_preferences(&reader, DEFAULT_USER_ID, None, body_bytes);
        assert_eq!(resp.status(), hyper::StatusCode::INTERNAL_SERVER_ERROR);
        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert!(body["code"].as_str().unwrap().contains("internal"));
    }

    #[test]
    fn test_set_preferences_malformed_body_returns_400() {
        let (_dir, reader, path) = setup_test_env();
        let body_bytes = Bytes::from("not valid json");
        let resp = set_user_preferences(&reader, DEFAULT_USER_ID, Some(&path), body_bytes);
        assert_eq!(resp.status(), hyper::StatusCode::BAD_REQUEST);
    }

    #[test]
    fn test_multiple_users_independent_preferences() {
        let (_dir, reader, path) = setup_test_env();

        // Set for user1
        let body = Bytes::from(serde_json::to_vec(&serde_json::json!({"theme": "dark"})).unwrap());
        set_user_preferences(&reader, "user1", Some(&path), body);

        // Set for user2
        let body = Bytes::from(serde_json::to_vec(&serde_json::json!({"theme": "light"})).unwrap());
        set_user_preferences(&reader, "user2", Some(&path), body);

        // Read user1 → dark
        let resp = get_user_preferences(&reader, "user1");
        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert_eq!(body["data"]["theme"], "dark");

        // Read user2 → light
        let resp = get_user_preferences(&reader, "user2");
        let body: serde_json::Value = serde_json::from_slice(resp.body()).unwrap();
        assert_eq!(body["data"]["theme"], "light");
    }
}
