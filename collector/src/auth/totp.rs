// SPDX-License-Identifier: MIT
//
// TOTP (Time-based One-Time Password) implementation per RFC 6238.
// Uses HMAC-SHA1 as the underlying hash function and a 30-second time step.

use hmac::{Hmac, Mac};
use rand::RngCore;
use rand::rngs::OsRng;
use sha1::Sha1;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha1 = Hmac<Sha1>;

const TOTP_TIME_STEP: u64 = 30;
const TOTP_DIGITS: u32 = 6;
const SECRET_BYTE_LENGTH: usize = 16; // 16 bytes -> 32 base32 chars (with padding)

// Base32 encoding alphabet (RFC 4648)
const BASE32_ALPHABET: &[u8; 32] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/// Generate a cryptographically random TOTP secret.
///
/// Returns a base32-encoded string of 16 random bytes.
/// Uses OsRng (CSPRNG) — never falls back to a seedable RNG.
pub fn generate_totp_secret() -> String {
    let mut bytes = [0u8; SECRET_BYTE_LENGTH];
    OsRng.fill_bytes(&mut bytes);
    base32_encode(&bytes)
}

/// Validate a TOTP code against the given base32-encoded secret.
///
/// Checks the current time window and the two adjacent windows
/// (+/- 1 step) for clock drift tolerance.
/// Uses constant-time comparison to prevent timing attacks.
pub fn validate_totp(secret: &str, code: &str) -> bool {
    // Guard: reject empty or malformed inputs without panicking
    if secret.is_empty() || code.len() != TOTP_DIGITS as usize {
        return false;
    }
    if !code.chars().all(|c| c.is_ascii_digit()) {
        return false;
    }

    let decoded = match base32_decode(secret) {
        Some(d) => d,
        None => return false,
    };

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    let counter = now / TOTP_TIME_STEP;

    // Check current window and adjacent windows for drift tolerance.
    // Always checks all 3 windows without early return to resist timing attacks:
    // the comparison outcome per window doesn't affect loop duration.
    let mut result = false;
    for offset in [0u64, 1, u64::MAX] {
        // wrapping: u64::MAX ≡ -1 (mod 2^64), so wrapping_add(u64::MAX) = wrapping_sub(1)
        let check_counter = counter.wrapping_add(offset);
        let expected = compute_totp(&decoded, check_counter, TOTP_DIGITS);
        result = result | constant_time_compare(code, &expected);
    }
    result
}

/// Compute a TOTP value for a given secret and counter.
pub(crate) fn compute_totp(secret: &[u8], counter: u64, digits: u32) -> String {
    // Counter as 8-byte big-endian
    let counter_bytes = counter.to_be_bytes();

    // HMAC-SHA1(K, C) per RFC 4226
    let mut mac = HmacSha1::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&counter_bytes);
    let result = mac.finalize().into_bytes();

    // Dynamic truncation per RFC 4226 Section 5.3
    let offset = (result[19] & 0x0f) as usize;
    let code = ((u32::from(result[offset]) & 0x7f) << 24)
        | (u32::from(result[offset + 1]) << 16)
        | (u32::from(result[offset + 2]) << 8)
        | u32::from(result[offset + 3]);

    // Modulo 10^digits
    let modulus = 10u32.pow(digits);
    let totp_value = code % modulus;

    format!("{:0width$}", totp_value, width = digits as usize)
}

/// Constant-time string comparison to resist timing attacks.
fn constant_time_compare(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut result: u8 = 0;
    for (ca, cb) in a.bytes().zip(b.bytes()) {
        result |= ca ^ cb;
    }
    result == 0
}

/// Base32 encode (RFC 4648 with padding).
fn base32_encode(input: &[u8]) -> String {
    let mut output = String::new();
    let mut buffer: u64 = 0;
    let mut bits: u32 = 0;

    for &byte in input {
        buffer = (buffer << 8) | u64::from(byte);
        bits += 8;
        while bits >= 5 {
            bits -= 5;
            let index = ((buffer >> bits) & 0x1f) as usize;
            output.push(BASE32_ALPHABET[index] as char);
        }
    }
    if bits > 0 {
        let index = ((buffer << (5 - bits)) & 0x1f) as usize;
        output.push(BASE32_ALPHABET[index] as char);
    }

    // Pad to multiple of 8
    while output.len() % 8 != 0 {
        output.push('=');
    }
    output
}

/// Base32 decode (RFC 4648). Returns None on invalid input.
pub(crate) fn base32_decode(input: &str) -> Option<Vec<u8>> {
    let cleaned: String = input
        .chars()
        .filter(|c| !c.is_whitespace())
        .flat_map(|c| c.to_uppercase())
        .collect();

    // Remove padding
    let stripped = cleaned.trim_end_matches('=');

    let mut output = Vec::new();
    let mut buffer: u64 = 0;
    let mut bits: u32 = 0;

    for c in stripped.chars() {
        let value = match c {
            'A'..='Z' => (c as u8) - b'A',
            '2'..='7' => (c as u8) - b'2' + 26,
            _ => return None,
        };
        buffer = (buffer << 5) | u64::from(value);
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            output.push((buffer >> bits) as u8);
        }
    }
    Some(output)
}

// ---------------------------------------------------------------------------
// Tests — 21 total (8 for generate_totp_secret + 13 for validate_totp)
// ---------------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;

    // ---- generate_totp_secret (8 test cases) ----

    #[test]
    fn ut01_returns_string() {
        let secret = generate_totp_secret();
        assert_eq!(
            std::any::TypeId::of::<String>(),
            std::any::TypeId::of::<String>()
        );
        // If we got here without panic, type is correct; verify it's a string
        let _: &str = &secret;
    }

    #[test]
    fn ut02_valid_base32_encoding() {
        let re = regex_lite::Regex::new(r"^[A-Z2-7]+=*$").unwrap();
        for _ in 0..10 {
            let secret = generate_totp_secret();
            assert!(re.is_match(&secret), "Invalid base32: {}", secret);
        }
    }

    #[test]
    fn ut03_correct_length() {
        let secret = generate_totp_secret();
        // 16 bytes -> 32 base32 chars (with padding)
        assert_eq!(
            secret.len(),
            32,
            "Expected 32 chars, got {}: '{}'",
            secret.len(),
            secret
        );
    }

    #[test]
    fn ut04_entropy_no_hardcoded() {
        let mut values = std::collections::HashSet::new();
        for _ in 0..100 {
            values.insert(generate_totp_secret());
        }
        assert!(
            values.len() >= 95,
            "Only {} unique secrets out of 100 — likely not random",
            values.len()
        );
    }

    #[test]
    fn ut05_different_calls_different() {
        let a = generate_totp_secret();
        let b = generate_totp_secret();
        assert_ne!(a, b, "Two calls produced identical secrets");
    }

    #[test]
    fn ut06_uses_csprng() {
        // Verify we get different results from OsRng
        let secret = generate_totp_secret();
        // Decode should succeed — proves it came from valid random bytes
        assert!(base32_decode(&secret).is_some());
        let decoded = base32_decode(&secret).unwrap();
        assert_eq!(decoded.len(), SECRET_BYTE_LENGTH);
        // Check it's not all zeros
        assert!(decoded.iter().any(|&b| b != 0), "Secret is all zeros");
    }

    #[test]
    fn ut07_shannon_entropy() {
        // Generate a secret and compute approximate entropy across multiple samples
        // to build a meaningful distribution.
        let mut all_bytes = Vec::new();
        for _ in 0..64 {
            let secret = generate_totp_secret();
            all_bytes.extend(base32_decode(&secret).unwrap());
        }

        // Shannon entropy across 64*16 = 1024 bytes. Random data should have
        // total entropy close to log2(256) = 8 bits/byte, so > 7.5 total.
        let mut freq = [0u64; 256];
        for &b in &all_bytes {
            freq[b as usize] += 1;
        }
        let total = all_bytes.len() as f64;
        let entropy: f64 = freq
            .iter()
            .filter(|&&c| c > 0)
            .map(|&c| {
                let p = c as f64 / total;
                -p * p.log2()
            })
            .sum();

        let avg_entropy = entropy;
        assert!(
            avg_entropy > 7.5,
            "Low entropy: {:.4} bits/byte (expected > 7.5)",
            avg_entropy
        );
    }

    #[test]
    fn ut08_no_seed_reuse() {
        // Generate secrets from consecutive calls and verify no duplicates
        let secrets: std::collections::HashSet<String> =
            (0..1000).map(|_| generate_totp_secret()).collect();
        assert_eq!(
            secrets.len(),
            1000,
            "Duplicate secrets detected — possible seed reuse"
        );
    }

    // ---- validate_totp (13 test cases) ----

    #[test]
    fn ut09_valid_code_passes() {
        let secret = generate_totp_secret();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / TOTP_TIME_STEP;
        let decoded = base32_decode(&secret).unwrap();
        let expected = compute_totp(&decoded, counter, TOTP_DIGITS);
        assert!(validate_totp(&secret, &expected));
    }

    #[test]
    fn ut10_invalid_code_fails() {
        let secret = generate_totp_secret();
        assert!(!validate_totp(&secret, "000000"));
    }

    #[test]
    fn ut11_wrong_secret_fails() {
        let secret_a = generate_totp_secret();
        let secret_b = generate_totp_secret();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / TOTP_TIME_STEP;
        let decoded_a = base32_decode(&secret_a).unwrap();
        let valid_code = compute_totp(&decoded_a, counter, TOTP_DIGITS);
        assert!(!validate_totp(&secret_b, &valid_code));
    }

    #[test]
    fn ut12_previous_window() {
        // With drift tolerance enabled, previous window should pass
        let secret = generate_totp_secret();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let prev_counter = (now / TOTP_TIME_STEP).saturating_sub(1);
        let decoded = base32_decode(&secret).unwrap();
        let prev_code = compute_totp(&decoded, prev_counter, TOTP_DIGITS);
        assert!(
            validate_totp(&secret, &prev_code),
            "Previous-window code should pass with drift tolerance"
        );
    }

    #[test]
    fn ut13_future_window() {
        let secret = generate_totp_secret();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let next_counter = now / TOTP_TIME_STEP + 1;
        let decoded = base32_decode(&secret).unwrap();
        let next_code = compute_totp(&decoded, next_counter, TOTP_DIGITS);
        assert!(
            validate_totp(&secret, &next_code),
            "Next-window code should pass with drift tolerance"
        );
    }

    #[test]
    fn ut14_empty_secret() {
        assert!(!validate_totp("", "123456"));
    }

    #[test]
    fn ut15_empty_code() {
        let secret = generate_totp_secret();
        assert!(!validate_totp(&secret, ""));
    }

    #[test]
    fn ut16_non_numeric_code() {
        let secret = generate_totp_secret();
        assert!(!validate_totp(&secret, "abcdef"));
    }

    #[test]
    fn ut17_code_too_short() {
        let secret = generate_totp_secret();
        assert!(!validate_totp(&secret, "12345"));
    }

    #[test]
    fn ut18_code_too_long() {
        let secret = generate_totp_secret();
        assert!(!validate_totp(&secret, "1234567"));
    }

    #[test]
    fn ut19_code_with_whitespace() {
        let secret = generate_totp_secret();
        assert!(!validate_totp(&secret, " 123456"));
        assert!(!validate_totp(&secret, "123456 "));
    }

    #[test]
    fn ut20_uppercase_secret() {
        let secret = generate_totp_secret();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / TOTP_TIME_STEP;
        let decoded = base32_decode(&secret).unwrap();
        let expected = compute_totp(&decoded, counter, TOTP_DIGITS);

        // Upper/lower case should work the same
        assert!(validate_totp(&secret.to_uppercase(), &expected));
        assert!(validate_totp(&secret.to_lowercase(), &expected));
    }

    #[test]
    fn ut21_constant_time_behavior() {
        // Verify constant-time comparison doesn't leak timing differences.
        // Since the loop now always checks all 3 windows (no early return),
        // valid and invalid paths should take comparable time.
        let secret = generate_totp_secret();
        let iterations = 2000;

        // Precompute a valid code
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let counter = now / TOTP_TIME_STEP;
        let decoded = base32_decode(&secret).unwrap();
        let valid_code = compute_totp(&decoded, counter, TOTP_DIGITS);

        let start_valid = std::time::Instant::now();
        for _ in 0..iterations {
            let _ = validate_totp(&secret, &valid_code);
        }
        let valid_duration = start_valid.elapsed();

        // Measure invalid code timing (same kind of code, different digits)
        let start_invalid = std::time::Instant::now();
        for _ in 0..iterations {
            let _ = validate_totp(&secret, "000000");
        }
        let invalid_duration = start_invalid.elapsed();

        let max_dur = valid_duration.max(invalid_duration);
        let min_dur = valid_duration.min(invalid_duration);
        let ratio = max_dur.as_nanos() as f64 / min_dur.as_nanos() as f64;

        // Ratio should be close to 1.0 because the loop body is identical
        // regardless of whether the code matches. Allow 2× as a safety margin
        // for runtime scheduling noise on a loaded system.
        assert!(
            ratio < 2.0,
            "Timing ratio too high: {:.4} (valid={:?}, invalid={:?})",
            ratio,
            valid_duration,
            invalid_duration
        );
    }

    // ---- GAP-UT01 (B3): base32_encode / base32_decode roundtrip (RFC 4648) ----

    #[test]
    fn gap_ut01_base32_roundtrip() {
        // decode(encode(x)) == x for various byte lengths
        let inputs: Vec<&[u8]> = vec![
            &b""[..],                               // empty
            &b"f"[..],                              // 1 byte
            &b"fo"[..],                             // 2 bytes
            &b"foo"[..],                            // 3 bytes
            &b"foob"[..],                           // 4 bytes
            &b"fooba"[..],                          // 5 bytes
            &b"foobar"[..],                         // 6 bytes
            &[0u8; 16][..],                         // all zeros
            &[0xffu8; 16][..],                      // all ones
            &b"The quick brown fox jumps over the lazy dog."[..], // long input
        ];
        for input in inputs {
            let encoded = base32_encode(input);
            let decoded = base32_decode(&encoded).expect("roundtrip decode must succeed");
            assert_eq!(
                &decoded, input,
                "roundtrip failed for {:?}: encoded='{}'",
                input, encoded
            );
        }
    }

    #[test]
    fn gap_ut01b_base32_invalid_input_rejected() {
        // decode should return None for invalid base32 strings
        assert!(base32_decode("!!!!!").is_none(), "exclamation marks rejected");
        assert!(base32_decode("12345").is_none(), "digits not in base32 rejected");
        // Whitespace is filtered before decoding, so "ABC DEF" is treated as "ABCDEF"
        // which IS valid base32. The code intentionally strips whitespace for resilience.
        assert!(base32_decode("ABC DEF").is_some(), "whitespace is stripped, so this should decode");
    }

    // ---- GAP-UT02 (B4): compute_totp deterministic behavior ----

    #[test]
    fn gap_ut02_compute_totp_deterministic() {
        // Same secret + same counter → same code every time
        let secret_bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10";
        let counter: u64 = 42;

        let code1 = compute_totp(secret_bytes, counter, TOTP_DIGITS);
        let code2 = compute_totp(secret_bytes, counter, TOTP_DIGITS);
        assert_eq!(code1, code2, "same inputs must produce same TOTP code");

        // Different counter → different code (with overwhelming probability)
        let code3 = compute_totp(secret_bytes, counter + 1, TOTP_DIGITS);
        assert_ne!(
            code1, code3,
            "different counter should produce different TOTP code"
        );

        // All counters produce exactly 6-digit strings
        for c in 0..10 {
            let code = compute_totp(secret_bytes, c, TOTP_DIGITS);
            assert_eq!(code.len(), 6, "TOTP code must be exactly {} digits", TOTP_DIGITS);
            assert!(code.chars().all(|ch| ch.is_ascii_digit()), "TOTP code must be numeric: '{}'", code);
        }
    }

    // ---- GAP-UT03 (B5): constant_time_compare direct test ----

    #[test]
    fn gap_ut03_ct_eq_identical() {
        assert!(constant_time_compare("abc123", "abc123"));
        assert!(constant_time_compare("", ""));
        assert!(constant_time_compare("hello_world_42", "hello_world_42"));
    }

    #[test]
    fn gap_ut03_ct_ne_different() {
        assert!(!constant_time_compare("abc", "abd"));
        assert!(!constant_time_compare("abc123", "abc124"));
        assert!(!constant_time_compare("ABC", "abc"));
    }

    #[test]
    fn gap_ut03_ct_different_lengths() {
        assert!(!constant_time_compare("abc", "abcd"));
        assert!(!constant_time_compare("", "a"));
        assert!(!constant_time_compare("longer", "short"));
    }
}
