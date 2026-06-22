# Test Plan: TOTP Two-Factor Authentication (2FA)

## 1. Overview

Add TOTP-based two-factor authentication to the user login flow. This document defines the complete test strategy, covering pure function unit tests and API integration tests.

### Architecture Context

- **Backend**: Rust (existing `collector/src/server/`) — the 2FA module will live in a new `collector/src/auth/` module
- **Frontend**: TypeScript/React (Next.js 15 + Vitest) — TOTP utility functions in `frontend/lib/auth/`
- **No existing auth layer**: This project has no authentication system today; all auth code is net-new

### Testing Stack

| Layer | Framework | Location |
|-------|-----------|----------|
| Backend unit tests | `cargo test` (inline `#[cfg(test)]`) | `collector/src/auth/` |
| Backend integration tests | `cargo test` (integration test suite) | `collector/tests/auth_2fa.rs` |
| Frontend unit tests | Vitest | `frontend/__tests__/auth/` |
| Security tests | `cargo test` (dedicated module) | `collector/src/auth/security_tests.rs` |

---

## 2. Success Criteria

- [ ] All unit tests pass (backend + frontend)
- [ ] All integration tests pass against staging environment
- [ ] Code coverage > 85% for all new 2FA code
- [ ] No regression in existing tests (`cargo test --all`)
- [ ] Security review: timing attack resistance, constant-time comparison, CSPRNG usage verified

---

## 3. Unit Test Specification — Agent B (Developer)

### 3.1 `generateTOTPSecret()`

| # | Test Case | Input | Expected Output |
|---|-----------|-------|-----------------|
| 1 | Returns a string | — | `typeof secret === "string"` |
| 2 | Valid base32 encoding | — | Matches `/^[A-Z2-7]+=*$/` |
| 3 | Correct length (16-byte → base32 padded to 32 chars) | — | `secret.length === 32` |
| 4 | Contains actual entropy (no hardcoded values) | Run 100× | At least 95 unique values |
| 5 | Different calls produce different secrets | Call twice | `secret1 !== secret2` |
| 6 | Cryptographically random (not Math.random) | — | Uses `crypto.randomBytes` or equivalent |
| 7 | Entropy check (Shannon) | — | Entropy > 3.5 bits/char |
| 8 | IDEMPOTENCY: same source of randomness does not produce same result | — | Confirms no seed reuse |

### 3.2 `validateTOTP(secret, code)`

| # | Test Case | Input | Expected Output |
|---|-----------|-------|-----------------|
| 1 | Valid TOTP code passes | `(secret, TOTP(secret, now))` | `true` |
| 2 | Invalid code fails | `(secret, "000000")` | `false` |
| 3 | Wrong secret fails | `(other_secret, valid_code)` | `false` |
| 4 | Code from previous time window | Window -1 | `false` (single-window) or `true` (with drift tolerance) — document the policy |
| 5 | Code from future time window | Window +1 | Same as #4 |
| 6 | Null/empty secret | `("", code)` | `false` (no panic) |
| 7 | Null/empty code | `(secret, "")` | `false` (no panic) |
| 8 | Non-numeric code | `(secret, "abcdef")` | `false` |
| 9 | Code too short (5 digits) | `(secret, "12345")` | `false` |
| 10 | Code too long (7 digits) | `(secret, "1234567")` | `false` |
| 11 | Code with whitespace | `(secret, " 123456")` | `false` |
| 12 | Uppercase base32 secret | `(SECRET.uppercase(), code)` | Same as lowercase |
| 13 | Constant-time: passing and failing paths take similar time | — | Max deviation < 15% of mean |

### 3.3 `generateBackupCodes(count)`

| # | Test Case | Input | Expected Output |
|---|-----------|-------|-----------------|
| 1 | Returns correct count | `(10)` | `codes.length === 10` |
| 2 | All codes are 8 digits | `(10)` | `codes.every(c => /^\d{8}$/.test(c))` |
| 3 | All codes unique | `(100)` | `new Set(codes).size === 100` |
| 4 | count = 0 | `(0)` | `[]` |
| 5 | count = 1 | `(1)` | `[code]` — valid single code |
| 6 | count = negative | `(-5)` | `[]` or throw — choose and document |
| 7 | count = very large (1000) | `(1000)` | All unique, all valid |
| 8 | No sequential pattern | `(10)` | Not simply `counter++` |
| 9 | Each call independent | Call twice with `(10)` | Intersection of sets is small (< 2) |

---

## 4. Integration Test Specification — Agent C (QA Engineer)

### 4.1 `POST /api/auth/enroll-2fa`

| # | Test Case | Auth State | Expected Status | Validation |
|---|-----------|-----------|-----------------|------------|
| 1 | Successful enrollment | Authenticated, 2FA off | **201** | Body contains `{ secret, qr_code_uri, backup_codes }` |
| 2 | Secret in response is valid base32 | Same as #1 | — | Secret passes same checks as `generateTOTPSecret` test #2 |
| 3 | QR code URI follows `otpauth://` scheme | Same as #1 | — | `qr_code_uri` matches `otpauth://totp/*?secret=*&issuer=*` |
| 4 | Backup codes returned | Same as #1 | — | `backup_codes` is array of 8-digit strings |
| 5 | Idempotency — enrolled again | Same session, already enrolled | **409** | Body contains error message |
| 6 | Unauthenticated | No auth token | **401** | Body contains auth error |
| 7 | Invalid session (expired token) | Expired JWT | **401** | — |
| 8 | Backup codes are unique within response | Same as #1 | — | `new Set(backup_codes).size === backup_codes.length` |

### 4.2 `POST /api/auth/verify-2fa`

| # | Test Case | Input | Expected Status | Validation |
|---|-----------|-------|-----------------|------------|
| 1 | Valid TOTP code — 2FA enabled | `{ code: totp(secret) }` | **200** | `{ enabled: true }`, user's 2FA flag now ON |
| 2 | Invalid TOTP code | `{ code: "000000" }` | **400** | Error message |
| 3 | Expired TOTP code (window outside tolerance) | Old code | **400** | Error message |
| 4 | Unauthenticated | No token | **401** | — |
| 5 | Already enrolled and verified | 2FA already ON | **409** | Error "already enabled" |
| 6 | No body / empty code | `{}` or `{ code: "" }` | **400** | Validation error |
| 7 | Valid backup code (first use) | `{ code: backup_code }` | **200** | 2FA enabled, backup code consumed |
| 8 | Reuse same backup code | Same backup code again | **400** | "Backup code already used" |
| 9 | Rate limit — 5 rapid failures | `{ code: "wrong1" }` ×5 | **429** After 5 attempts | Rate limit header present |

### 4.3 `POST /api/auth/login`

| # | Test Case | Credentials | 2FA State | Expected Status | Validation |
|---|-----------|-------------|-----------|-----------------|------------|
| 1 | Login without 2FA | password only | Not enrolled | **200** | JWT without `2fa` claim |
| 2 | Login with 2FA + valid TOTP | password + totp | Enrolled + verified | **200** | JWT contains `"2fa": true` claim |
| 3 | Login with 2FA + valid backup code | password + backup | Enrolled + verified | **200** | JWT contains `"2fa": true`, backup consumed |
| 4 | Login with 2FA — missing TOTP | password only | Enrolled + verified | **400** | "TOTP code required" |
| 5 | Login with 2FA — invalid TOTP | password + wrong totp | Enrolled + verified | **401** | "Invalid credentials" |
| 6 | Wrong password | wrong password | Any | **401** | No information leak (same message as #5) |
| 7 | Non-existent user | unknown@user | — | **401** | Same message as #5/#6 (no user enumeration) |
| 8 | JWT structure valid | — | — | — | Contains `sub`, `iat`, `exp`, `2fa` (if applicable) |
| 9 | JWT expiry reasonable | — | — | — | `exp - iat` ≤ 24h (or configured TTL) |
| 10 | Rate limit — 10 rapid failures | password + wrong totp ×10 | — | **429** | Account temporarily locked |

---

## 5. End-to-End Scenario Test

Required end-to-end flow to validate the complete 2FA lifecycle:

```
1. User registers (or exists in system)                     → 200
2. POST /api/auth/enroll-2fa                                → 201 { secret, backup_codes, qr_code_uri }
3. Generate valid TOTP code from secret                     → 6-digit code
4. POST /api/auth/verify-2fa { code }                       → 200 { enabled: true }
5. POST /api/auth/login { password, totp: new_totp }        → 200 { token } with 2fa claim
6. Access protected resource with valid JWT                  → 200
7. POST /api/auth/login { password }  (no TOTP)             → 400 "TOTP required"
8. POST /api/auth/login { password, totp: backup_code }      → 200 { token } (backup consumed)
```

---

## 6. Security Test Specification

### 6.1 Timing Attack Resistance

| # | Test Case | Method |
|---|-----------|--------|
| 1 | TOTP comparison is constant-time | Use `constant_time_eq` or similar; verify no early-return on first byte mismatch |
| 2 | Backup code comparison is constant-time | Same as above |
| 3 | Login timing does not reveal 2FA status | Compare response time for "wrong password" vs "correct password but missing 2FA" — must be indistinguishable |

### 6.2 Cryptographic Soundness

| # | Test Case | Validation |
|---|-----------|------------|
| 1 | Secret generation uses `OsRng` / `getrandom` | Code review — no `seedable_rng` or `std::time` as seed |
| 2 | Backup codes are hashed before storage | Verify storage only contains `SHA-256(backup_code)` |
| 3 | TOTP secret is encrypted at rest in DB | Confirm encryption before persistence |

### 6.3 Output Safety

| # | Test Case | Validation |
|---|-----------|------------|
| 1 | QR code URI does not appear in server logs | Integration test with log capture |
| 2 | Secret not returned after enrollment completes | `GET /api/auth/2fa-status` (if exists) doesn't leak secret |
| 3 | Backup codes only returned once at enrollment | Subsequent `enroll-2fa` (if allowed) returns new codes; old ones invalidated |

---

## 7. Test Infrastructure Setup

### Backend (`collector/`)

```
collector/src/auth/
├── mod.rs              # Module exports
├── totp.rs             # TOTP generation / validation
├── backup_codes.rs     # Backup code generation
├── api.rs              # HTTP handlers for /api/auth/*
└── security_tests.rs   # Timing attack + crypto tests
```

**Cargo dependencies to add (dev):**
- `totp-rs` or `oath` crate (TOTP implementation)
- `hex`, `base32` (encoding)
- `serde_json` (already in dev-deps)

### Frontend (`frontend/`)

```
frontend/lib/auth/
├── totp.ts         # TOTP secret generation (pure wrapping)
├── totp.test.ts    # Vitest unit tests
└── backup_codes.ts # Backup code UI helpers
```

**Frontend dependencies:**
- No additional deps — use Web Crypto API (`crypto.subtle`) + `@levischuck/tiny-totp` or manual implementation

---

## 8. Task Distribution

| ID | Assignee | Area | Description | Est. Effort |
|----|----------|------|-------------|-------------|
| UT-01 | Agent B | Backend | `generateTOTPSecret` unit tests (8 test cases) | Small |
| UT-02 | Agent B | Backend | `validateTOTP` unit tests (13 test cases) | Medium |
| UT-03 | Agent B | Backend | `generateBackupCodes` unit tests (9 test cases) | Small |
| UT-04 | Agent B | Frontend | TOTP frontend utility unit tests | Small |
| IT-01 | Agent C | Backend | `POST /api/auth/enroll-2fa` integration tests (8 test cases) | Medium |
| IT-02 | Agent C | Backend | `POST /api/auth/verify-2fa` integration tests (9 test cases) | Medium |
| IT-03 | Agent C | Backend | `POST /api/auth/login` integration tests (10 test cases) | Medium |
| IT-04 | Agent C | Backend | End-to-end scenario test (8-step lifecycle) | Medium |
| ST-01 | Agent C | Security | Timing attack + crypto soundness tests (6 test cases) | Medium |

---

## 9. Test Report

After execution, fill the report template:

```
Test Plan: 2FA Feature
Date: [DATE]
Executor: [AGENT NAME]

Unit Tests:  [PASS/FAIL]  (N/N passed)
Integration: [PASS/FAIL]  (N/N passed)
Security:    [PASS/FAIL]  (N/N passed)
Coverage:    XX% (target: >85%)

Issues Found:
- [Description] (severity: HIGH/MED/LOW)
```
