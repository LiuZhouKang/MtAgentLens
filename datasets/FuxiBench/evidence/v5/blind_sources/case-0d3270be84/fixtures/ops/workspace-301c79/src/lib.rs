//! Duration parsing helpers used by the operations toolkit.
//!
//! The toolkit accepts human-readable durations in maintenance plans, retention
//! policies and scheduler overrides. This crate implements the parser shared by
//! those components. The grammar and error semantics are documented in
//! `docs.md`; that document is the authoritative contract.

use std::time::Duration;

/// Errors returned by [`parse_duration`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DurationError {
    /// The input was empty after trimming ASCII whitespace.
    Empty,
    /// A new segment started where a count was required.
    MissingNumber,
    /// A count was not followed by a unit.
    MissingUnit,
    /// A count had leading zeros (`01s`) or did not fit in `u64`.
    InvalidNumber,
    /// The unit word is not one of `ns`, `us`, `ms`, `s`, `m`, `h`, `d`.
    UnknownUnit,
    /// Segments were not written from the largest unit to the smallest.
    UnitOutOfOrder,
    /// The same unit appeared more than once.
    DuplicateUnit,
    /// The count or the accumulated duration exceeds the representable range.
    Overflow,
}

/// Parse a compact duration such as `1h30m`, `250ms` or `2d4h`.
///
/// # Grammar
///
/// ```text
/// duration  = segment+
/// segment   = count unit
/// count     = "0" | [1-9] DIGIT*          (no sign, no leading zeros)
/// unit      = "ns" | "us" | "ms" | "s" | "m" | "h" | "d"   (ASCII, case-insensitive)
/// ```
///
/// The whole input is trimmed of ASCII whitespace before parsing. Inside the
/// input no whitespace or separators are allowed. Units must appear in strictly
/// descending order (`1h30m` is valid, `30m1h` is not) and each unit may appear
/// at most once.
///
/// # Errors
///
/// See [`DurationError`]. In particular a count that does not fit in `u64`
/// yields [`DurationError::Overflow`], and so does a total that exceeds
/// `u64::MAX` seconds plus 999 999 999 nanoseconds.
pub fn parse_duration(input: &str) -> Result<Duration, DurationError> {
    let trimmed = input.trim_matches(|c: char| c.is_ascii_whitespace());
    if trimmed.is_empty() {
        return Err(DurationError::Empty);
    }

    let bytes = trimmed.as_bytes();
    let mut index = 0usize;
    let mut previous_rank: Option<u8> = None;
    let mut total_nanos: u128 = 0;

    while index < bytes.len() {
        let digits_start = index;
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            index += 1;
        }
        if index == digits_start {
            return Err(DurationError::MissingNumber);
        }
        let digits = &trimmed[digits_start..index];
        if digits.len() > 1 && digits.starts_with('0') {
            return Err(DurationError::InvalidNumber);
        }
        let count: u64 = digits.parse().map_err(|_| DurationError::Overflow)?;

        let unit_start = index;
        while index < bytes.len() && bytes[index].is_ascii_alphabetic() {
            index += 1;
        }
        if index == unit_start {
            return Err(DurationError::MissingUnit);
        }
        let unit = &trimmed[unit_start..index];

        let (rank, nanos_per_unit): (u8, u128) = match unit.to_ascii_lowercase().as_str() {
            "ns" => (0, 1),
            "us" => (1, 1_000),
            "ms" => (2, 1_000_000),
            "s" => (3, 1_000_000_000),
            "m" => (4, 60 * 1_000_000_000),
            "h" => (5, 3_600 * 1_000_000_000),
            "d" => (6, 86_400 * 1_000_000_000),
            _ => return Err(DurationError::UnknownUnit),
        };

        if let Some(previous) = previous_rank {
            if rank == previous {
                return Err(DurationError::DuplicateUnit);
            }
            if rank > previous {
                return Err(DurationError::UnitOutOfOrder);
            }
        }
        previous_rank = Some(rank);

        total_nanos = total_nanos
            .checked_add(u128::from(count) * nanos_per_unit)
            .ok_or(DurationError::Overflow)?;
        if total_nanos > u128::from(u64::MAX) * 1_000_000_000 + 999_999_999 {
            return Err(DurationError::Overflow);
        }
    }

    let seconds = (total_nanos / 1_000_000_000) as u64;
    let nanos = (total_nanos % 1_000_000_000) as u32;
    Ok(Duration::new(seconds, nanos))
}

#[cfg(test)]
mod internal_tests {
    use super::*;

    #[test]
    fn parses_a_single_segment() {
        assert_eq!(parse_duration("250ms"), Ok(Duration::from_millis(250)));
    }

    #[test]
    fn parses_the_documented_compound_example() {
        assert_eq!(parse_duration("1h30m"), Ok(Duration::from_secs(5_400)));
    }
}
