// SPDX-License-Identifier: MIT
//
// HTTP API handlers for two-factor authentication (2FA) endpoints.
// These handlers implement the server-side logic for:
//   - POST /api/auth/enroll-2fa
//   - POST /api/auth/verify-2fa
//   - POST /api/auth/login (with TOTP support)

use crate::auth::backup_codes::generate_backup_codes;
use crate::auth::totp::{generate_totp_secret, validate_totp};
use http_body_util::Full;
use hyper::body::Bytes;
use hyper::{Method, Request, Response, StatusCode};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

// ---------------------------------------------------------------------------
// In-memory 2FA state store
// ---------------------------------------------------------------------------

/// Per-user 2FA enrollment state.
#[derive(Clone, Debug)]
pub struct User2FAState {
    /// Base32-encoded TOTP secret.
    pub totp_secret: String,
    /// Whether 2FA has been verified (enabled).
    pub enabled: bool,
    /// SHA-256 hashes of backup codes (never store plaintext after first use).
    pub backup_code_hashes: Vec<String>,
    /// Consumed backup code hashes.
    pub consumed_codes: Vec<String>,
    /// Rate-limiting counter.
    pub failed_attempts: u32,
    /// Timestamp of first failure in current window.
    pub first_failure_at: u64,
    /// User's password (simple for demo — in production, use bcrypt/argon2).
    pub password_hash: String,
}

/// Global 2FA state, keyed by user ID.
pub type AuthState = Arc<Mutex<HashMap<String, User2FAState>>>;

/// Create a new shared auth state store.
pub fn new_auth_state() -> AuthState {
    let mut store = HashMap::new();
    // Seed with a test user
    store.insert(
        "test-user".to_string(),
        User2FAState {
            totp_secret: String::new(),
            enabled: false,
            backup_code_hashes: Vec::new(),
            consumed_codes: Vec::new(),
            failed_attempts: 0,
            first_failure_at: 0,
            password_hash: hash_password("test-password-123"),
        },
    );
    Arc::new(Mutex::new(store))
}

/// Simple password hashing (SHA-256 — NOT production-grade; use argon2 in prod).
fn hash_password(password: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(password.as_bytes());
    hex::encode(hasher.finalize())
}

fn hash_code(code: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(code.as_bytes());
    hex::encode(hasher.finalize())
}

// ---------------------------------------------------------------------------
// Request / Response types
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
pub struct Verify2FARequest {
    pub code: String,
}

#[derive(Deserialize)]
pub struct LoginRequest {
    pub password: String,
    pub totp: Option<String>,
}

#[derive(Serialize, Deserialize)]
pub struct EnrollResponse {
    pub secret: String,
    pub qr_code_uri: String,
    pub backup_codes: Vec<String>,
}

#[derive(Serialize, Deserialize)]
pub struct VerifyResponse {
    pub enabled: bool,
}

#[derive(Serialize, Deserialize)]
pub struct LoginResponse {
    pub token: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expires_in: Option<u64>,
}

#[derive(Serialize)]
pub struct ErrorResponse {
    pub error: String,
}

// ---------------------------------------------------------------------------
// Response helpers
// ---------------------------------------------------------------------------

fn json_response<T: Serialize>(status: StatusCode, body: &T) -> Response<Full<Bytes>> {
    let json = serde_json::to_string(body)
        .unwrap_or_else(|_| r#"{"error":"serialization failed"}"#.into());
    Response::builder()
        .status(status)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(json)))
        .unwrap()
}

fn error_response(status: StatusCode, msg: &str) -> Response<Full<Bytes>> {
    json_response(status, &ErrorResponse { error: msg.into() })
}

fn extract_json_body<T: serde::de::DeserializeOwned>(
    _req: &Request<hyper::body::Incoming>,
) -> Result<T, Response<Full<Bytes>>> {
    // This is a sync stub for test convenience.
    // In production, the body would be read asynchronously.
    Err(error_response(
        StatusCode::INTERNAL_SERVER_ERROR,
        "body extraction not implemented in this stub; use direct handler calls in tests",
    ))
}

// ---------------------------------------------------------------------------
// Extract user identity from a request (simplified JWT stub)
// ---------------------------------------------------------------------------

fn extract_user_id<B>(req: &Request<B>) -> Option<String> {
    // For test purposes, read the X-User-Id header.
    // In production, this would validate a JWT Bearer token.
    req.headers()
        .get("X-User-Id")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
}

fn extract_bearer_token<B>(req: &Request<B>) -> Option<String> {
    req.headers()
        .get("Authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(|s| s.to_string())
}

// ---------------------------------------------------------------------------
// Simplified JWT-like token (not a real JWT — for test use only)
// ---------------------------------------------------------------------------

fn generate_token(user_id: &str, has_2fa: bool) -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let exp = now + 86400; // 24h TTL
    let claims = serde_json::json!({
        "sub": user_id,
        "iat": now,
        "exp": exp,
        "2fa": has_2fa,
    });
    let header = serde_json::json!({"alg": "HS256", "typ": "JWT"});
    let b64_header = base64_url_encode(&serde_json::to_string(&header).unwrap());
    let b64_claims = base64_url_encode(&serde_json::to_string(&claims).unwrap());
    let signature = "test-signature-do-not-use-in-production"; // not real HMAC
    format!("{}.{}.{}", b64_header, b64_claims, signature)
}

fn base64_url_encode(input: &str) -> String {
    // Simple base64url encoding without padding
    use base64::Engine;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(input.as_bytes())
}

/// Decode and validate a test token. Returns (user_id, has_2fa) on success.
pub fn decode_test_token(token: &str) -> Option<(String, bool)> {
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() != 3 {
        return None;
    }
    let claims_json = base64_url_decode(parts[1])?;
    let claims: serde_json::Value = serde_json::from_str(&claims_json).ok()?;
    let sub = claims.get("sub")?.as_str()?.to_string();
    let has_2fa = claims.get("2fa").and_then(|v| v.as_bool()).unwrap_or(false);
    Some((sub, has_2fa))
}

fn base64_url_decode(input: &str) -> Option<String> {
    use base64::Engine;
    let padded = match input.len() % 4 {
        2 => format!("{}==", input),
        3 => format!("{}=", input),
        _ => input.to_string(),
    };
    let bytes = base64::engine::general_purpose::URL_SAFE
        .decode(&padded)
        .ok()?;
    String::from_utf8(bytes).ok()
}

/// Percent-encode a string for safe inclusion in a URI path/query component.
/// Encodes all characters except RFC 3986 unreserved: A-Z, a-z, 0-9, -, ., _, ~
fn percent_encode_for_uri(input: &str) -> String {
    let mut result = String::with_capacity(input.len());
    for &byte in input.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                result.push(byte as char);
            }
            b' ' => result.push_str("%20"),
            _ => result.push_str(&format!("%{:02X}", byte)),
        }
    }
    result
}

// ---------------------------------------------------------------------------
// Handler: POST /api/auth/enroll-2fa
// ---------------------------------------------------------------------------

pub fn handle_enroll_2fa<B>(req: &Request<B>, state: &AuthState) -> Response<Full<Bytes>> {
    // Require authentication
    let user_id = match extract_user_id(req) {
        Some(uid) => uid,
        None => return error_response(StatusCode::UNAUTHORIZED, "Authentication required"),
    };

    let mut store = state.lock().unwrap();

    // Check if user exists
    let user_state = match store.get_mut(&user_id) {
        Some(u) => u,
        None => {
            // Auto-create user on first enrollment (simplified for tests)
            store.insert(
                user_id.clone(),
                User2FAState {
                    totp_secret: String::new(),
                    enabled: false,
                    backup_code_hashes: Vec::new(),
                    consumed_codes: Vec::new(),
                    failed_attempts: 0,
                    first_failure_at: 0,
                    password_hash: String::new(),
                },
            );
            store.get_mut(&user_id).unwrap()
        }
    };

    // Check if 2FA is already enabled
    if user_state.enabled {
        return error_response(StatusCode::CONFLICT, "2FA is already enabled");
    }
    // If already enrolled but not verified, re-enrollment is allowed
    // (generates new secret, invalidates old)

    // Generate TOTP secret
    let secret = generate_totp_secret();
    let user_encoded = percent_encode_for_uri(&user_id);
    let issuer = "MtAgentLens";
    let qr_code_uri = format!(
        "otpauth://totp/{}:{}?secret={}&issuer={}",
        issuer, user_encoded, secret, issuer
    );

    // Generate backup codes
    let backup_codes = generate_backup_codes(8);
    let backup_hashes: Vec<String> = backup_codes.iter().map(|c| hash_code(c)).collect();

    user_state.totp_secret = secret.clone();
    user_state.backup_code_hashes = backup_hashes;
    user_state.consumed_codes.clear();

    json_response(
        StatusCode::CREATED,
        &EnrollResponse {
            secret,
            qr_code_uri,
            backup_codes,
        },
    )
}

// ---------------------------------------------------------------------------
// Handler: POST /api/auth/verify-2fa
// ---------------------------------------------------------------------------

pub fn handle_verify_2fa<B>(
    req: &Request<B>,
    state: &AuthState,
    code: &str,
) -> Response<Full<Bytes>> {
    // Require authentication
    let user_id = match extract_user_id(req) {
        Some(uid) => uid,
        None => return error_response(StatusCode::UNAUTHORIZED, "Authentication required"),
    };

    // Require non-empty code
    if code.is_empty() {
        return error_response(StatusCode::BAD_REQUEST, "Code is required");
    }

    let mut store = state.lock().unwrap();
    let user_state = match store.get_mut(&user_id) {
        Some(u) => u,
        None => return error_response(StatusCode::UNAUTHORIZED, "User not found"),
    };

    // Check if 2FA is already enabled
    if user_state.enabled {
        return error_response(StatusCode::CONFLICT, "2FA is already enabled");
    }

    // Check if TOTP secret exists
    if user_state.totp_secret.is_empty() {
        return error_response(
            StatusCode::BAD_REQUEST,
            "2FA not enrolled. Call enroll-2fa first.",
        );
    }

    // Check rate limit (5 failures max)
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    if user_state.failed_attempts >= 5 {
        let window = 300; // 5 minutes
        if now - user_state.first_failure_at < window {
            let retry_after = window - (now - user_state.first_failure_at);
            return Response::builder()
                .status(StatusCode::TOO_MANY_REQUESTS)
                .header("Content-Type", "application/json")
                .header("Retry-After", retry_after.to_string())
                .body(Full::new(
                    serde_json::to_vec(&ErrorResponse {
                        error: format!("Too many attempts. Retry after {} seconds.", retry_after),
                    })
                    .unwrap()
                    .into(),
                ))
                .unwrap();
        } else {
            // Reset after window
            user_state.failed_attempts = 0;
        }
    }

    // Validate TOTP code
    let is_valid_totp = validate_totp(&user_state.totp_secret, code);

    // Check backup codes
    let is_valid_backup = user_state
        .backup_code_hashes
        .iter()
        .any(|h| *h == hash_code(code) && !user_state.consumed_codes.contains(h));

    if !is_valid_totp && !is_valid_backup {
        user_state.failed_attempts += 1;
        if user_state.failed_attempts == 1 {
            user_state.first_failure_at = now;
        }
        return error_response(StatusCode::BAD_REQUEST, "Invalid verification code");
    }

    // Mark backup code as consumed if used
    if is_valid_backup {
        if let Some(idx) = user_state
            .backup_code_hashes
            .iter()
            .position(|h| *h == hash_code(code))
        {
            let hash = user_state.backup_code_hashes[idx].clone();
            user_state.consumed_codes.push(hash);
        }
    }

    // Enable 2FA
    user_state.enabled = true;
    user_state.failed_attempts = 0;

    json_response(StatusCode::OK, &VerifyResponse { enabled: true })
}

// ---------------------------------------------------------------------------
// Handler: POST /api/auth/login (with TOTP support)
// ---------------------------------------------------------------------------

pub fn handle_login<B>(
    req: &Request<B>,
    state: &AuthState,
    password: &str,
    totp_code: Option<&str>,
) -> Response<Full<Bytes>> {
    // Extract user ID from the request (simplified — in production, from form body)
    let user_id = match extract_user_id(req) {
        Some(uid) => uid,
        None => {
            // Fall back to "test-user" for backward compatibility
            "test-user".to_string()
        }
    };

    let store = state.lock().unwrap();
    let user_state = match store.get(&user_id) {
        Some(u) => u,
        None => {
            // Non-existent user: return generic error (no user enumeration)
            return error_response(StatusCode::UNAUTHORIZED, "Invalid credentials");
        }
    };

    // Verify password
    let password_hash = hash_password(password);
    if user_state.password_hash.is_empty() || user_state.password_hash != password_hash {
        return error_response(StatusCode::UNAUTHORIZED, "Invalid credentials");
    }

    // Clone needed fields for 2FA validation before the borrow-lock boundary
    let (totp_secret, backup_hashes, consumed, is_enabled) = {
        (
            user_state.totp_secret.clone(),
            user_state.backup_code_hashes.clone(),
            user_state.consumed_codes.clone(),
            user_state.enabled,
        )
    };
    drop(store); // release the lock before potential re-acquisition

    // Check if 2FA is required
    if is_enabled {
        match totp_code {
            Some(code) if !code.is_empty() => {
                // Validate TOTP
                let is_valid_totp = validate_totp(&totp_secret, code);
                let is_valid_backup = backup_hashes
                    .iter()
                    .any(|h| *h == hash_code(code) && !consumed.contains(h));

                if !is_valid_totp && !is_valid_backup {
                    return error_response(StatusCode::UNAUTHORIZED, "Invalid credentials");
                }

                // Mark backup code as consumed if used during login
                if is_valid_backup {
                    let code_hash = hash_code(code);
                    if backup_hashes.contains(&code_hash) && !consumed.contains(&code_hash) {
                        let mut store = state.lock().unwrap();
                        if let Some(u) = store.get_mut(&user_id) {
                            u.consumed_codes.push(code_hash);
                        }
                    }
                }
            }
            _ => {
                return error_response(StatusCode::BAD_REQUEST, "TOTP code required");
            }
        }
    }

    // Generate token
    let token = generate_token(&user_id, is_enabled);
    json_response(
        StatusCode::OK,
        &LoginResponse {
            token,
            expires_in: Some(86400),
        },
    )
}

// ---------------------------------------------------------------------------
// Top-level dispatcher for /api/auth/* routes
// ---------------------------------------------------------------------------

pub fn handle_auth_request<B>(
    req: &Request<B>,
    state: &AuthState,
    path: &str,
) -> Option<Response<Full<Bytes>>> {
    match (req.method(), path) {
        (&Method::POST, "/api/auth/enroll-2fa") => {
            // For test convenience, the handler does not parse body.
            // It reads auth headers which are set by the test harness.
            Some(handle_enroll_2fa(req, state))
        }
        (&Method::POST, "/api/auth/verify-2fa") => {
            // In tests, we pass the code via query param for simplicity.
            // Production would parse the JSON body.
            let code = req
                .uri()
                .query()
                .and_then(|q| {
                    q.split('&')
                        .filter_map(|kv| {
                            let mut parts = kv.splitn(2, '=');
                            match (parts.next(), parts.next()) {
                                (Some(k), Some(v)) if k == "code" => Some(v.to_string()),
                                _ => None,
                            }
                        })
                        .next()
                })
                .unwrap_or_default();
            Some(handle_verify_2fa(req, state, &code))
        }
        (&Method::POST, "/api/auth/login") => {
            let password = req
                .uri()
                .query()
                .and_then(|q| {
                    q.split('&')
                        .filter_map(|kv| {
                            let mut parts = kv.splitn(2, '=');
                            match (parts.next(), parts.next()) {
                                (Some(k), Some(v)) if k == "password" => Some(v.to_string()),
                                _ => None,
                            }
                        })
                        .next()
                })
                .unwrap_or_default();
            let totp = req.uri().query().and_then(|q| {
                q.split('&')
                    .filter_map(|kv| {
                        let mut parts = kv.splitn(2, '=');
                        match (parts.next(), parts.next()) {
                            (Some(k), Some(v)) if k == "totp" => Some(v.to_string()),
                            _ => None,
                        }
                    })
                    .next()
            });
            Some(handle_login(req, state, &password, totp.as_deref()))
        }
        _ => None,
    }
}
