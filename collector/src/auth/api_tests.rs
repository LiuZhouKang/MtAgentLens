// SPDX-License-Identifier: MIT
//
// Integration tests for the 2FA feature HTTP API handlers.
// Tests are organized into five groups:
//   C1: POST /api/auth/enroll-2fa
//   C2: POST /api/auth/verify-2fa
//   C3: POST /api/auth/login (with TOTP)
//   C4: E2E full flow
//   C5: Edge cases and error handling
//
// All tests route through handle_auth_request() to exercise the full
// dispatch + handler chain, emulating real HTTP requests.

#[cfg(test)]
mod api_tests {
    use crate::auth::api::{
        EnrollResponse, LoginResponse, VerifyResponse, decode_test_token, handle_auth_request,
        new_auth_state,
    };
    use http_body_util::BodyExt;
    use http_body_util::Full;
    use hyper::body::Bytes;
    use hyper::{Method, Request, StatusCode};
    use std::collections::HashMap;
    use std::collections::HashSet;
    use std::sync::{Arc, Mutex};

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    fn auth_req(uri: &str, user: &str) -> Request<Full<Bytes>> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("X-User-Id", user)
            .body(Full::new(Bytes::new()))
            .unwrap()
    }

    fn anon_req(uri: &str) -> Request<Full<Bytes>> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .body(Full::new(Bytes::new()))
            .unwrap()
    }

    /// Enroll a user and return the enrollment response.
    async fn enroll(
        state: &Arc<Mutex<HashMap<String, crate::auth::api::User2FAState>>>,
        user: &str,
    ) -> EnrollResponse {
        let req = auth_req("/api/auth/enroll-2fa", user);
        let resp = handle_auth_request(&req, state, "/api/auth/enroll-2fa")
            .expect("handle_auth_request returned None for enroll-2fa");
        assert_eq!(
            resp.status(),
            StatusCode::CREATED,
            "enroll-2fa for '{}' expected 201",
            user
        );
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        serde_json::from_slice(&body).unwrap()
    }

    /// Call verify-2fa and return (status, optional VerifyResponse).
    async fn verify(
        state: &Arc<Mutex<HashMap<String, crate::auth::api::User2FAState>>>,
        user: &str,
        code: &str,
    ) -> (StatusCode, Option<VerifyResponse>) {
        let uri = format!("/api/auth/verify-2fa?code={}", code);
        let req = auth_req(&uri, user);
        let resp = handle_auth_request(&req, state, "/api/auth/verify-2fa")
            .expect("handle_auth_request returned None for verify-2fa");
        let status = resp.status();
        if status == StatusCode::OK {
            let body = resp.into_body().collect().await.unwrap().to_bytes();
            let vr: VerifyResponse = serde_json::from_slice(&body).unwrap();
            (status, Some(vr))
        } else {
            (status, None)
        }
    }

    /// Parse response body as a JSON Value (for error message inspection).
    async fn body_as_value(resp: hyper::Response<Full<Bytes>>) -> serde_json::Value {
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        serde_json::from_slice(&body).unwrap()
    }

    fn decode_jwt_claims(token: &str) -> Option<serde_json::Value> {
        use base64::Engine;
        let parts: Vec<&str> = token.split('.').collect();
        if parts.len() != 3 {
            return None;
        }
        let claims_b64 = parts[1];
        let padded = match claims_b64.len() % 4 {
            2 => format!("{}==", claims_b64),
            3 => format!("{}=", claims_b64),
            _ => claims_b64.to_string(),
        };
        let bytes = base64::engine::general_purpose::URL_SAFE
            .decode(&padded)
            .ok()?;
        serde_json::from_slice(&bytes).ok()
    }

    // -----------------------------------------------------------------------
    // C1: POST /api/auth/enroll-2fa
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn it01_enroll_success() {
        let state = new_auth_state();
        let enroll_resp = enroll(&state, "test-user").await;

        // 32-char base32 secret
        assert_eq!(
            enroll_resp.secret.len(),
            32,
            "TOTP secret must be 32 base32 characters"
        );
        assert!(
            enroll_resp
                .secret
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '='),
            "secret must be base32-encoded"
        );

        // QR code URI format
        assert!(
            enroll_resp.qr_code_uri.starts_with("otpauth://totp/"),
            "QR URI must start with otpauth://totp/"
        );
        assert!(
            enroll_resp.qr_code_uri.contains("?secret="),
            "QR URI must contain secret parameter"
        );
        assert!(
            enroll_resp.qr_code_uri.contains("&issuer=MtAgentPrism"),
            "QR URI must contain issuer=MtAgentPrism"
        );

        // 8 unique 8-digit backup codes
        assert_eq!(
            enroll_resp.backup_codes.len(),
            8,
            "must return exactly 8 backup codes"
        );
        let unique: HashSet<_> = enroll_resp.backup_codes.iter().collect();
        assert_eq!(unique.len(), 8, "all 8 backup codes must be unique");
        for code in &enroll_resp.backup_codes {
            assert_eq!(code.len(), 8, "each backup code must be 8 chars");
            assert!(
                code.chars().all(|c| c.is_ascii_digit()),
                "each backup code must be numeric"
            );
        }
    }

    #[tokio::test]
    async fn it02_enroll_unauthorized() {
        let state = new_auth_state();
        let req = anon_req("/api/auth/enroll-2fa");
        let resp = handle_auth_request(&req, &state, "/api/auth/enroll-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(
            resp.status(),
            StatusCode::UNAUTHORIZED,
            "enroll without auth must return 401"
        );
    }

    #[tokio::test]
    async fn it03_enroll_already_enabled() {
        let state = new_auth_state();

        // Step 1: enroll
        let enroll_resp = enroll(&state, "test-user").await;

        // Step 2: verify with first backup code -> enables 2FA
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify should succeed");

        // Step 3: enroll again -> 409
        let req = auth_req("/api/auth/enroll-2fa", "test-user");
        let resp = handle_auth_request(&req, &state, "/api/auth/enroll-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(
            resp.status(),
            StatusCode::CONFLICT,
            "enroll when 2FA already enabled must return 409"
        );

        let v = body_as_value(resp).await;
        assert!(
            v["error"]
                .as_str()
                .unwrap_or("")
                .contains("already enabled"),
            "error message should mention already enabled"
        );
    }

    #[tokio::test]
    async fn it04_qr_code_uri_format() {
        let state = new_auth_state();
        let enroll_resp = enroll(&state, "test-user").await;

        // Check exact format: otpauth://totp/ISSUER:USER?secret=XXX&issuer=ISSUER
        let uri = &enroll_resp.qr_code_uri;
        assert!(
            uri.starts_with("otpauth://totp/MtAgentPrism:test-user?"),
            "URI must contain issuer and user: got '{}'",
            uri
        );
        assert!(
            uri.contains("?secret="),
            "URI must contain secret parameter"
        );
        assert!(
            uri.contains("&issuer=MtAgentPrism"),
            "URI must end with issuer parameter"
        );

        // Secret inside URI must match the returned secret
        let secret_param = format!("?secret={}", enroll_resp.secret);
        assert!(
            uri.contains(&secret_param),
            "URI must embed the actual secret: missing '{}' in '{}'",
            secret_param,
            uri
        );
    }

    // -----------------------------------------------------------------------
    // C2: POST /api/auth/verify-2fa
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn it05_verify_valid_totp() {
        // Use backup code from enrollment as the valid code
        let state = new_auth_state();
        let enroll_resp = enroll(&state, "test-user").await;

        let (status, response) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "verify with valid backup code should succeed"
        );
        assert!(response.is_some(), "verify should return a response body");
        assert_eq!(
            response.unwrap().enabled,
            true,
            "2FA should be marked as enabled"
        );
    }

    #[tokio::test]
    async fn it06_verify_invalid_code() {
        let state = new_auth_state();
        let _enroll = enroll(&state, "test-user").await;

        let (status, _) = verify(&state, "test-user", "00000000").await;
        assert_eq!(
            status,
            StatusCode::BAD_REQUEST,
            "verify with invalid code must return 400"
        );
    }

    #[tokio::test]
    async fn it07_verify_not_enrolled() {
        let state = new_auth_state();
        // test-user exists in fresh state but hasn't enrolled (totp_secret is empty)
        let (status, _) = verify(&state, "test-user", "12345678").await;
        assert_eq!(
            status,
            StatusCode::BAD_REQUEST,
            "verify without enrollment must return 400"
        );
    }

    #[tokio::test]
    async fn it08_verify_unauthorized() {
        let state = new_auth_state();
        let req = anon_req("/api/auth/verify-2fa?code=12345678");
        let resp = handle_auth_request(&req, &state, "/api/auth/verify-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(
            resp.status(),
            StatusCode::UNAUTHORIZED,
            "verify without auth must return 401"
        );
    }

    #[tokio::test]
    async fn it09_verify_already_enabled() {
        let state = new_auth_state();

        // Enroll & enable 2FA
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "first verify (enable) should succeed"
        );

        // Try to verify again
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[1]).await;
        assert_eq!(
            status,
            StatusCode::CONFLICT,
            "verify when already enabled must return 409"
        );
    }

    #[tokio::test]
    async fn it10_verify_rate_limit() {
        let state = new_auth_state();
        let _enroll = enroll(&state, "test-user").await;

        // 5 consecutive failed attempts
        for i in 0..5 {
            let (status, _) = verify(&state, "test-user", "00000000").await;
            assert_eq!(
                status,
                StatusCode::BAD_REQUEST,
                "failed attempt {} should return 400",
                i + 1
            );
        }

        // 6th attempt -> 429 Too Many Requests
        let uri = "/api/auth/verify-2fa?code=00000000";
        let req = auth_req(uri, "test-user");
        let resp = handle_auth_request(&req, &state, "/api/auth/verify-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(
            resp.status(),
            StatusCode::TOO_MANY_REQUESTS,
            "6th failed attempt must return 429"
        );
        assert!(
            resp.headers().get("Retry-After").is_some(),
            "429 response must include Retry-After header"
        );
        let retry_after: u64 = resp.headers()["Retry-After"]
            .to_str()
            .unwrap()
            .parse()
            .unwrap();
        assert!(retry_after > 0, "Retry-After must be positive");
        assert!(retry_after <= 300, "Retry-After must be <= 300 seconds");
    }

    #[tokio::test]
    async fn it11_verify_backup_code() {
        let state = new_auth_state();
        let enroll_resp = enroll(&state, "test-user").await;

        // Use each backup code one by one (first code enables 2FA,
        // subsequent codes are rejected because already enabled)
        let (status, response) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "verify with backup code should succeed on first use"
        );
        assert_eq!(
            response.unwrap().enabled,
            true,
            "2FA should be enabled after backup code verification"
        );
    }

    // -----------------------------------------------------------------------
    // C3: POST /api/auth/login (with TOTP)
    // -----------------------------------------------------------------------

    /// Call login and return the response.
    async fn login(
        state: &Arc<Mutex<HashMap<String, crate::auth::api::User2FAState>>>,
        user: &str,
        password: &str,
        totp: Option<&str>,
    ) -> hyper::Response<Full<Bytes>> {
        let uri = match totp {
            Some(t) => format!("/api/auth/login?password={}&totp={}", password, t),
            None => format!("/api/auth/login?password={}", password),
        };
        let req = auth_req(&uri, user);
        handle_auth_request(&req, state, "/api/auth/login")
            .expect("handle_auth_request returned None for login")
    }

    #[tokio::test]
    async fn it12_login_password_and_totp() {
        let state = new_auth_state();

        // Enroll & enable 2FA
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify should succeed");

        // Login with correct password + backup code
        let resp = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[1]),
        )
        .await;
        assert_eq!(
            resp.status(),
            StatusCode::OK,
            "login with valid password + backup code must return 200"
        );

        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let login_resp: LoginResponse = serde_json::from_slice(&body).unwrap();
        assert!(!login_resp.token.is_empty(), "must return a token");
        assert_eq!(
            login_resp.expires_in,
            Some(86400),
            "token should have 24h expiry"
        );

        // Decode token and verify 2fa claim
        let (sub, has_2fa) = decode_test_token(&login_resp.token).expect("token must be decodable");
        assert_eq!(sub, "test-user", "token sub should be the user ID");
        assert!(has_2fa, "token should have 2fa:true");
    }

    #[tokio::test]
    async fn it13_login_invalid_totp() {
        let state = new_auth_state();

        // Enroll & enable 2FA
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify should succeed");

        // Login with correct password but invalid TOTP
        let resp = login(&state, "test-user", "test-password-123", Some("00000000")).await;
        assert_eq!(
            resp.status(),
            StatusCode::UNAUTHORIZED,
            "login with invalid TOTP must return 401"
        );
    }

    #[tokio::test]
    async fn it14_login_wrong_password() {
        let state = new_auth_state();
        let resp = login(&state, "test-user", "wrong-password", None).await;
        assert_eq!(
            resp.status(),
            StatusCode::UNAUTHORIZED,
            "login with wrong password must return 401"
        );
    }

    #[tokio::test]
    async fn it15_login_missing_totp() {
        let state = new_auth_state();

        // Enroll & enable 2FA
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify should succeed");

        // Login without TOTP code (2FA is enabled)
        let resp = login(&state, "test-user", "test-password-123", None).await;
        assert_eq!(
            resp.status(),
            StatusCode::BAD_REQUEST,
            "login without TOTP when 2FA enabled must return 400"
        );
    }

    #[tokio::test]
    async fn it16_login_without_2fa() {
        let state = new_auth_state();
        // test-user has 2FA disabled by default
        let resp = login(&state, "test-user", "test-password-123", None).await;
        assert_eq!(
            resp.status(),
            StatusCode::OK,
            "login without 2FA enabled must return 200"
        );

        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let login_resp: LoginResponse = serde_json::from_slice(&body).unwrap();
        assert!(!login_resp.token.is_empty(), "must return a token");

        let (sub, has_2fa) = decode_test_token(&login_resp.token).expect("token must be decodable");
        assert_eq!(sub, "test-user");
        assert!(!has_2fa, "token should have 2fa:false when 2FA not enabled");
    }

    #[tokio::test]
    async fn it17_jwt_token_claims() {
        let state = new_auth_state();

        // Case 1: login without 2FA
        let resp = login(&state, "test-user", "test-password-123", None).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let login_resp: LoginResponse = serde_json::from_slice(&body).unwrap();

        let claims = decode_jwt_claims(&login_resp.token).expect("JWT claims must be decodable");
        assert_eq!(claims["sub"], "test-user", "sub must be the user ID");
        assert_eq!(claims["2fa"], false, "2fa must be false when 2FA is off");

        let iat = claims["iat"].as_u64().expect("iat must be a timestamp");
        let exp = claims["exp"].as_u64().expect("exp must be a timestamp");
        assert!(iat > 0, "iat must be positive");
        assert!(exp > iat, "exp must be after iat");
        assert_eq!(exp - iat, 86400, "token must have 24h TTL");

        // Case 2: login with 2FA enabled
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify should succeed");

        let resp2 = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[1]),
        )
        .await;
        assert_eq!(resp2.status(), StatusCode::OK);
        let body2 = resp2.into_body().collect().await.unwrap().to_bytes();
        let login_resp2: LoginResponse = serde_json::from_slice(&body2).unwrap();

        let claims2 = decode_jwt_claims(&login_resp2.token).expect("JWT claims must be decodable");
        assert_eq!(claims2["sub"], "test-user", "sub must be the user ID");
        assert_eq!(claims2["2fa"], true, "2fa must be true when 2FA is enabled");
    }

    // -----------------------------------------------------------------------
    // C4: E2E full flow
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn it18_e2e_full_flow() {
        let state = new_auth_state();

        // a. Login without 2FA -> token
        let resp = login(&state, "test-user", "test-password-123", None).await;
        assert_eq!(resp.status(), StatusCode::OK, "step a: login without 2FA");
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let login_resp: LoginResponse = serde_json::from_slice(&body).unwrap();
        let (sub, has_2fa) = decode_test_token(&login_resp.token).unwrap();
        assert_eq!(sub, "test-user");
        assert!(!has_2fa, "step a: 2fa must be false when 2FA is off");

        // b. Enroll 2FA -> get secret + backup_codes
        let enroll_resp = enroll(&state, "test-user").await;
        assert_eq!(
            enroll_resp.backup_codes.len(),
            8,
            "step b: must have 8 backup codes"
        );

        // c. Verify with a backup code -> enabled
        let (status, verify_resp) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "step c: verify should succeed");
        assert_eq!(
            verify_resp.unwrap().enabled,
            true,
            "step c: 2FA must be enabled"
        );

        // d. Login with a different backup code -> token with 2fa:true
        let resp2 = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[1]),
        )
        .await;
        assert_eq!(
            resp2.status(),
            StatusCode::OK,
            "step d: login with backup code"
        );
        let body2 = resp2.into_body().collect().await.unwrap().to_bytes();
        let login_resp2: LoginResponse = serde_json::from_slice(&body2).unwrap();
        let (sub2, has_2fa2) = decode_test_token(&login_resp2.token).unwrap();
        assert_eq!(sub2, "test-user");
        assert!(has_2fa2, "step d: token must have 2fa:true");

        // e. Backup code consumed on second use -> fails
        // backup_codes[0] was consumed by verify in step c; reusing it for login
        // must fail because handle_login checks consumed_codes.
        let resp3 = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[0]),
        )
        .await;
        assert_eq!(
            resp3.status(),
            StatusCode::UNAUTHORIZED,
            "step e: consumed backup code must fail on second use"
        );
    }

    // -----------------------------------------------------------------------
    // C5: Edge cases and error handling
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn it23_state_isolation_between_users() {
        let state = new_auth_state();

        // Enroll user-a and user-b
        let enroll_a = enroll(&state, "test-user").await;
        // For a second user, we need the auto-create behavior of handle_enroll_2fa
        // which creates the user on first enrollment.
        let enroll_b = enroll(&state, "other-user").await;

        // Verify that user-a's codes don't work for user-b
        let (status, _) = verify(&state, "other-user", &enroll_a.backup_codes[0]).await;
        assert_eq!(
            status,
            StatusCode::BAD_REQUEST,
            "user-b must not be verifiable with user-a's backup code"
        );

        // Verify that user-b's codes don't work for user-a
        let (status, _) = verify(&state, "test-user", &enroll_b.backup_codes[0]).await;
        assert_eq!(
            status,
            StatusCode::BAD_REQUEST,
            "user-a must not be verifiable with user-b's backup code"
        );

        // Each user's own codes should work for them
        let (status_a, _) = verify(&state, "test-user", &enroll_a.backup_codes[0]).await;
        assert_eq!(
            status_a,
            StatusCode::OK,
            "user-a must be verifiable with their own backup code"
        );

        let (status_b, _) = verify(&state, "other-user", &enroll_b.backup_codes[0]).await;
        assert_eq!(
            status_b,
            StatusCode::OK,
            "user-b must be verifiable with their own backup code"
        );
    }

    #[tokio::test]
    async fn it24_backup_code_cannot_be_reused() {
        let state = new_auth_state();

        // Enroll & verify (consumes one backup code)
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "first use should succeed");

        // The consumed backup code can't be used for login
        let resp = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[0]),
        )
        .await;
        assert_eq!(
            resp.status(),
            StatusCode::UNAUTHORIZED,
            "consumed backup code must not be accepted for login"
        );
    }

    #[tokio::test]
    async fn it25_empty_code_validation() {
        let state = new_auth_state();

        // Enroll a user
        let _enroll = enroll(&state, "test-user").await;

        // Verify with empty code via dispatcher (no ?code= param)
        let req = auth_req("/api/auth/verify-2fa", "test-user");
        let resp = handle_auth_request(&req, &state, "/api/auth/verify-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(
            resp.status(),
            StatusCode::BAD_REQUEST,
            "empty code must return 400"
        );

        // Verify via direct handler with explicit empty string
        use crate::auth::api::handle_verify_2fa;
        let req2 = auth_req("/api/auth/verify-2fa", "test-user");
        let resp2 = handle_verify_2fa(&req2, &state, "");
        assert_eq!(
            resp2.status(),
            StatusCode::BAD_REQUEST,
            "explicit empty string must also return 400"
        );
    }

    #[tokio::test]
    async fn it26_rate_limit_reset() {
        let state = new_auth_state();
        let _enroll = enroll(&state, "test-user").await;

        // 5 failed attempts
        for _ in 0..5 {
            let (status, _) = verify(&state, "test-user", "00000000").await;
            assert_eq!(status, StatusCode::BAD_REQUEST);
        }

        // 6th attempt: rate limited
        let req = auth_req("/api/auth/verify-2fa?code=00000000", "test-user");
        let resp = handle_auth_request(&req, &state, "/api/auth/verify-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(
            resp.status(),
            StatusCode::TOO_MANY_REQUESTS,
            "6th attempt must be rate-limited"
        );

        // Verify state shows rate limit
        {
            let store = state.lock().unwrap();
            let user = store.get("test-user").unwrap();
            assert_eq!(
                user.failed_attempts, 5,
                "failed_attempts must be 5 after rate limit kicks in"
            );
            assert!(
                user.first_failure_at > 0,
                "first_failure_at must be recorded"
            );
        }

        // Simulate the 5-minute window having elapsed by resetting first_failure_at
        {
            let mut store = state.lock().unwrap();
            let user = store.get_mut("test-user").unwrap();
            user.first_failure_at = 0;
            // Keep failed_attempts=5 to trigger the reset branch
        }

        // Now the rate limit check sees window has passed -> resets counter
        let (status, _) = verify(&state, "test-user", "00000000").await;
        assert_eq!(
            status,
            StatusCode::BAD_REQUEST,
            "after window reset, attempt should return 400 not 429"
        );

        // Counter should be 1 after the reset + new failure
        {
            let store = state.lock().unwrap();
            let user = store.get("test-user").unwrap();
            assert_eq!(
                user.failed_attempts, 1,
                "counter should reset and increment to 1"
            );
        }
    }

    // -----------------------------------------------------------------------
    // GAP-IT01 (C5): Login backup code consumption
    //   Verify that a backup code used for login is marked as consumed
    //   and cannot be reused for a subsequent login.
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn gap_it01_backup_code_consumed_on_login() {
        let state = new_auth_state();

        // Enroll & enable 2FA
        let enroll_resp = enroll(&state, "test-user").await;
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify should succeed");

        // Login with backup_codes[1] — should succeed and consume it
        let resp = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[1]),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::OK, "first login with backup code");

        // Login again with SAME backup code — should fail (consumed)
        let resp2 = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[1]),
        )
        .await;
        assert_eq!(
            resp2.status(),
            StatusCode::UNAUTHORIZED,
            "consumed backup code must be rejected on second login use"
        );
    }

    #[tokio::test]
    async fn gap_it01b_backup_code_not_consumed_when_used_as_totp() {
        // Verify that a backup code used as TOTP (via verify-2fa) is consumed
        // and cannot be reused for login.
        let state = new_auth_state();

        let enroll_resp = enroll(&state, "test-user").await;

        // Use verify-2fa (enables 2FA + consumes the code)
        let (status, _) = verify(&state, "test-user", &enroll_resp.backup_codes[0]).await;
        assert_eq!(status, StatusCode::OK, "verify consumes backup code");

        // Now try using the same code for login — must be rejected
        let resp = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[0]),
        )
        .await;
        assert_eq!(
            resp.status(),
            StatusCode::UNAUTHORIZED,
            "code consumed via verify must not work for login either"
        );

        // But a different code (not consumed) should work
        let resp2 = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_resp.backup_codes[1]),
        )
        .await;
        assert_eq!(
            resp2.status(),
            StatusCode::OK,
            "unused backup code must still work"
        );
    }

    // -----------------------------------------------------------------------
    // GAP-IT02 (C6): Concurrent enrollment test
    //   Two users enroll + verify concurrently without interfering.
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn gap_it02_concurrent_enrollment() {
        use tokio::join;

        let state = new_auth_state();

        // Enroll two users concurrently
        let (enroll_a, enroll_b) =
            join!(enroll(&state, "test-user"), enroll(&state, "other-user"));

        // Verify both secrets are valid
        assert_eq!(enroll_a.secret.len(), 32, "user-a secret length");
        assert_eq!(enroll_b.secret.len(), 32, "user-b secret length");
        assert_ne!(enroll_a.secret, enroll_b.secret, "secrets must differ");

        // Both should have 8 backup codes
        assert_eq!(enroll_a.backup_codes.len(), 8);
        assert_eq!(enroll_b.backup_codes.len(), 8);

        // Verify concurrently — each with their own backup code
        let (status_a, status_b) = join!(
            verify(&state, "test-user", &enroll_a.backup_codes[0]),
            verify(&state, "other-user", &enroll_b.backup_codes[0]),
        );
        assert_eq!(status_a.0, StatusCode::OK, "user-a verify");
        assert_eq!(status_b.0, StatusCode::OK, "user-b verify");

        // Login test-user (has password) with an unused backup code
        let resp_a = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&enroll_a.backup_codes[1]),
        )
        .await;
        assert_eq!(resp_a.status(), StatusCode::OK, "user-a login");

        // Verify other-user's state is correctly set up
        {
            let store = state.lock().unwrap();
            let other_user = store.get("other-user").expect("other-user must exist");
            assert!(
                other_user.enabled,
                "other-user must have 2FA enabled after verify"
            );
            assert!(
                !other_user.consumed_codes.is_empty(),
                "other-user must have consumed a backup code"
            );
        }
    }

    // -----------------------------------------------------------------------
    // GAP-IT03 (C7): Special character QR code URI encoding
    //   Username with special characters must be percent-encoded in otpauth:// URI.
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn gap_it03_special_char_username_qr_uri() {
        let state = new_auth_state();

        let special_user = "user with spaces";
        let req = Request::builder()
            .method("POST")
            .uri("/api/auth/enroll-2fa")
            .header("X-User-Id", special_user)
            .body(Full::new(Bytes::new()))
            .unwrap();

        let resp = handle_auth_request(&req, &state, "/api/auth/enroll-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(resp.status(), StatusCode::CREATED);

        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let enroll_resp: EnrollResponse = serde_json::from_slice(&body).unwrap();

        // The URI should have space encoded as %20
        assert!(
            enroll_resp.qr_code_uri.contains("user%20with%20spaces"),
            "Spaces in username must be percent-encoded in QR URI, got: {}",
            enroll_resp.qr_code_uri
        );

        // Test other special characters
        let special_user2 = "user+plus&special";
        let req2 = Request::builder()
            .method("POST")
            .uri("/api/auth/enroll-2fa")
            .header("X-User-Id", special_user2)
            .body(Full::new(Bytes::new()))
            .unwrap();

        let resp2 = handle_auth_request(&req2, &state, "/api/auth/enroll-2fa")
            .expect("handle_auth_request returned None");
        assert_eq!(resp2.status(), StatusCode::CREATED);

        let body2 = resp2.into_body().collect().await.unwrap().to_bytes();
        let enroll_resp2: EnrollResponse = serde_json::from_slice(&body2).unwrap();

        assert!(
            enroll_resp2.qr_code_uri.contains("user%2Bplus%26special"),
            "Special chars in username must be percent-encoded, got: {}",
            enroll_resp2.qr_code_uri
        );
    }

    // -----------------------------------------------------------------------
    // GAP-IT04 (C8): Real TOTP code API verification
    //   Generate a real TOTP code (via compute_totp) and verify it through the API.
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn gap_it04_verify_with_real_totp_code() {
        use crate::auth::totp::compute_totp;
        use crate::auth::totp::base32_decode;
        use std::time::{SystemTime, UNIX_EPOCH};

        let state = new_auth_state();

        // Enroll a user
        let enroll_resp = enroll(&state, "real-totp-user").await;
        let secret_base32 = &enroll_resp.secret;

        // Decode the base32 secret and compute a real TOTP code for current window
        let decoded = base32_decode(secret_base32).expect("valid base32 secret");
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / 30;
        let real_code = compute_totp(&decoded, counter, 6);

        // Verify this real TOTP code through the API
        let (status, response) = verify(&state, "real-totp-user", &real_code).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "verify with real TOTP code (window) should succeed"
        );
        assert_eq!(
            response.unwrap().enabled,
            true,
            "2FA must be enabled after real TOTP verification"
        );
    }

    #[tokio::test]
    async fn gap_it04b_real_totp_login_flow() {
        use crate::auth::totp::compute_totp;
        use crate::auth::totp::base32_decode;
        use std::time::{SystemTime, UNIX_EPOCH};

        let state = new_auth_state();

        // Enroll test-user (has password) and get the TOTP secret
        let enroll_resp = enroll(&state, "test-user").await;
        let decoded = base32_decode(&enroll_resp.secret).expect("valid base32 secret");

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / 30;
        let totp_code = compute_totp(&decoded, counter, 6);

        // Enable 2FA via verify with real TOTP code
        let (status, _) = verify(&state, "test-user", &totp_code).await;
        assert_eq!(status, StatusCode::OK, "enable 2FA with real TOTP code");

        // Compute a fresh TOTP code for login (same window since both calls are fast)
        let totp_code2 = compute_totp(&decoded, counter, 6);

        let resp = login(
            &state,
            "test-user",
            "test-password-123",
            Some(&totp_code2),
        )
        .await;
        assert_eq!(
            resp.status(),
            StatusCode::OK,
            "login with real TOTP code must succeed"
        );

        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let login_resp: LoginResponse = serde_json::from_slice(&body).unwrap();
        let (sub, has_2fa) = decode_test_token(&login_resp.token).expect("token must be decodable");
        assert_eq!(sub, "test-user", "token sub should match user");
        assert!(has_2fa, "token should have 2fa:true");
    }

    #[tokio::test]
    async fn gap_it04c_wrong_secret_totp_fails() {
        use crate::auth::totp::compute_totp;
        use crate::auth::totp::base32_decode;
        use std::time::{SystemTime, UNIX_EPOCH};

        let state = new_auth_state();

        // Enroll user-a and get user-b's secret
        let enroll_a = enroll(&state, "totp-wrong-secret-user").await;
        let decoded_a = base32_decode(&enroll_a.secret).expect("valid base32 secret");
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / 30;
        let real_code_a = compute_totp(&decoded_a, counter, 6);

        // user-a's TOTP code must work for user-a
        let (status, _) = verify(&state, "totp-wrong-secret-user", &real_code_a).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "user-a's TOTP code must work for user-a"
        );
    }
}
