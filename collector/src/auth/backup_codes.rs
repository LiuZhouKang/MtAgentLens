// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// Backup code generation for two-factor authentication.
// Each backup code is a cryptographically random 8-digit number.

use rand::RngCore;
use rand::rngs::OsRng;

/// Generate a single 8-digit backup code.
fn generate_single_code() -> String {
    loop {
        // Generate 4 random bytes and interpret as u32
        let mut bytes = [0u8; 4];
        OsRng.fill_bytes(&mut bytes);
        let value = u32::from_be_bytes(bytes) as u64;

        // Map to 8-digit range [10_000_000, 99_999_999]
        let code = 10_000_000 + (value % 90_000_000);
        let formatted = format!("{:08}", code);

        // Ensure it's exactly 8 digits and doesn't start with 0
        if formatted.len() == 8 && formatted.as_bytes()[0] != b'0' {
            return formatted;
        }
    }
}

/// Generate `count` unique 8-digit backup codes.
///
/// Returns `Vec<String>` with exactly `count` elements.
/// If `count <= 0`, returns an empty `Vec`.
///
/// Uses OsRng (CSPRNG) — never falls back to a seedable RNG.
/// Each code is generated independently, ensuring no sequential pattern.
pub fn generate_backup_codes(count: i32) -> Vec<String> {
    if count <= 0 {
        return Vec::new();
    }

    let mut codes = Vec::with_capacity(count as usize);
    let mut seen = std::collections::HashSet::new();
    let target = count as usize;

    while seen.len() < target {
        let code = generate_single_code();
        if seen.insert(code.clone()) {
            codes.push(code);
        }
    }

    codes
}

// ---------------------------------------------------------------------------
// Tests — 9 test cases for generate_backup_codes
// ---------------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ut22_returns_correct_count() {
        let codes = generate_backup_codes(10);
        assert_eq!(codes.len(), 10);
    }

    #[test]
    fn ut23_all_codes_8_digits() {
        let codes = generate_backup_codes(100);
        for code in &codes {
            assert_eq!(code.len(), 8, "Code '{}' is not 8 digits", code);
            assert!(
                code.chars().all(|c| c.is_ascii_digit()),
                "Code '{}' has non-digit chars",
                code
            );
        }
    }

    #[test]
    fn ut24_all_codes_unique() {
        let codes = generate_backup_codes(100);
        let unique: std::collections::HashSet<_> = codes.iter().collect();
        assert_eq!(unique.len(), 100, "Duplicate codes found in 100 samples");
    }

    #[test]
    fn ut25_count_zero() {
        let codes = generate_backup_codes(0);
        assert!(codes.is_empty());
    }

    #[test]
    fn ut26_count_one() {
        let codes = generate_backup_codes(1);
        assert_eq!(codes.len(), 1);
        assert_eq!(codes[0].len(), 8);
        assert!(codes[0].chars().all(|c| c.is_ascii_digit()));
    }

    #[test]
    fn ut27_count_negative() {
        let codes = generate_backup_codes(-5);
        assert!(codes.is_empty(), "Negative count should return empty vec");
    }

    #[test]
    fn ut28_large_count() {
        let codes = generate_backup_codes(1000);
        assert_eq!(
            codes.len(),
            1000,
            "Expected 1000 codes, got {}",
            codes.len()
        );
        let unique: std::collections::HashSet<_> = codes.iter().collect();
        assert_eq!(unique.len(), 1000, "Duplicates found in 1000 codes");
    }

    #[test]
    fn ut29_no_sequential_pattern() {
        let codes = generate_backup_codes(50);
        // Sort numeric and check no sequential run of 3+
        let mut sorted: Vec<u64> = codes.iter().map(|c| c.parse::<u64>().unwrap()).collect();
        sorted.sort();

        for window in sorted.windows(3) {
            let diff1 = window[1] - window[0];
            let diff2 = window[2] - window[1];
            // If both diffs are 1, it's a sequential pattern
            assert!(
                !(diff1 == 1 && diff2 == 1),
                "Sequential pattern detected: {}, {}, {}",
                window[0],
                window[1],
                window[2]
            );
        }
    }

    #[test]
    fn ut30_independent_calls() {
        let codes_a: std::collections::HashSet<String> =
            generate_backup_codes(10).into_iter().collect();
        let codes_b: std::collections::HashSet<String> =
            generate_backup_codes(10).into_iter().collect();

        // Intersection should be very small (ideally 0)
        let intersection: Vec<_> = codes_a.intersection(&codes_b).collect();
        assert!(
            intersection.len() < 2,
            "Too much overlap between independent calls: {} shared codes",
            intersection.len()
        );
    }
}
