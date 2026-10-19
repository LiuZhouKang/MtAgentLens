// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

/// Timestamp conversion utilities.
///
/// Internally, collector events use nanoseconds since UNIX epoch.
/// Presentation layers may derive millisecond timestamps from that canonical value.
use std::fs;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

/// Cached boot time in seconds since UNIX epoch
static BOOT_TIME_SECS: OnceLock<i64> = OnceLock::new();

/// Get the system boot time in seconds since UNIX epoch
///
/// This reads from /proc/stat (btime field) and caches the result.
/// Falls back to calculating from /proc/uptime if btime is not available.
pub fn get_boot_time_secs() -> i64 {
    *BOOT_TIME_SECS.get_or_init(|| {
        // Try to read from /proc/stat (most reliable)
        if let Ok(content) = fs::read_to_string("/proc/stat") {
            for line in content.lines() {
                if line.starts_with("btime ")
                    && let Some(btime_str) = line.split_whitespace().nth(1)
                    && let Ok(btime) = btime_str.parse::<i64>()
                {
                    return btime;
                }
            }
        }

        // Fallback: calculate from uptime
        if let Ok(uptime_str) = fs::read_to_string("/proc/uptime")
            && let Some(uptime_secs_str) = uptime_str.split_whitespace().next()
            && let Ok(uptime_secs) = uptime_secs_str.parse::<f64>()
        {
            let now_secs = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs() as i64;
            return now_secs - uptime_secs as i64;
        }

        // Last resort: return current time (will be incorrect but won't crash)
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64
    })
}

/// Convert nanoseconds since boot to nanoseconds since UNIX epoch.
///
/// This is used to convert eBPF timestamps (from `bpf_ktime_get_ns()`) into a
/// wall-clock aligned value while preserving nanosecond precision for internal
/// correlation windows.
pub fn boot_ns_to_epoch_ns(ns_since_boot: u64) -> u64 {
    let boot_time_secs = get_boot_time_secs() as u64;
    (boot_time_secs * 1_000_000_000).saturating_add(ns_since_boot)
}

/// Convert nanoseconds since UNIX epoch to milliseconds since UNIX epoch.
pub fn epoch_ns_to_epoch_ms(epoch_ns: u64) -> u64 {
    epoch_ns / 1_000_000
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_boot_time_is_reasonable() {
        let boot_time = get_boot_time_secs();
        // Boot time should be in the past (less than current time)
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        assert!(boot_time < now);
        // Boot time should be reasonable (after year 2020)
        assert!(boot_time > 1577836800); // 2020-01-01
    }

    #[test]
    fn test_boot_ns_to_epoch_ns_conversion() {
        let ns_since_boot = 1000_000_000_000u64;
        let result_ns = boot_ns_to_epoch_ns(ns_since_boot);

        let boot_time_ns = get_boot_time_secs() as u64 * 1_000_000_000;
        let expected_ns = boot_time_ns + ns_since_boot;

        assert_eq!(result_ns, expected_ns);
    }

    #[test]
    fn test_epoch_ns_to_epoch_ms_conversion() {
        assert_eq!(
            epoch_ns_to_epoch_ms(1_234_567_890_123_456_789),
            1_234_567_890_123
        );
    }
}
