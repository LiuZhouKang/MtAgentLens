# Feature Specification: Two-Factor Authentication (2FA)

## Overview
Add TOTP-based two-factor authentication to the user login flow.

## Requirements

### Unit Tests (Agent B — Developer)
Test the following pure functions:
- `generateTOTPSecret()`: Returns a valid base32-encoded 16-byte secret
- `validateTOTP(secret, code)`: Returns true for valid codes, false otherwise
- `generateBackupCodes(count)`: Returns `count` unique 8-digit codes

### Integration Tests (Agent C — QA Engineer)
Test the following API endpoints:
- `POST /api/auth/enroll-2fa`: Creates TOTP secret, returns QR code URI
- `POST /api/auth/verify-2fa`: Validates TOTP code, enables 2FA on account
- `POST /api/auth/login`: Accepts password + TOTP, returns JWT with 2FA claim

### Test Report Template
```
Test Plan: 2FA Feature
Date: [DATE]
Executor: [AGENT NAME]

Unit Tests:  [PASS/FAIL]  (N/N passed)
Integration: [PASS/FAIL]  (N/N passed)
Coverage:    XX% (target: >85%)

Issues Found:
- [Description] (severity: HIGH/MED/LOW)
```

### Success Criteria
- All unit tests pass
- All integration tests pass against staging environment
- Code coverage > 85% for new code
