// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

//! Process-target command construction for the raw/trace subcommands.
//!
//! These helpers live in the library, not in `main.rs`, so the integration
//! tests exercise the exact code the collector runs. A private copy in a test
//! file once kept asserting flags the helper no longer accepts.

/// Explicit FILTER mode: capture only processes matching `-c`/`-p` and their
/// descendants (see `bpftools/include/process_filter.h`).
pub const FILTER_MODE: u32 = 2;

/// Expand `--pid-set` into per-PID process helpers.
///
/// A pid set wins over a single `--pid`; an empty set falls back to it.
pub fn resolve_pid_targets(pid: Option<u32>, pid_set: &[u32]) -> Vec<u32> {
    if !pid_set.is_empty() {
        return pid_set.to_vec();
    }
    pid.into_iter().collect()
}

/// Build the arguments for one `process` helper instance.
///
/// A filtered run selects FILTER mode explicitly. The helper happens to switch
/// to FILTER when it parses `-c`/`-p`, but the capture scope is a product
/// decision and must not depend on that parsing side effect. An explicit
/// `--mode` still wins. No filter means the helper's all-process default.
pub fn build_process_args(
    comm: Option<&str>,
    pid: Option<u32>,
    duration: Option<u32>,
    mode: Option<u32>,
    verbose: bool,
) -> Vec<String> {
    let mut args = Vec::new();
    if let Some(comm_filter) = comm {
        args.extend(["-c".to_string(), comm_filter.to_string()]);
    }
    if let Some(pid_filter) = pid {
        args.extend(["-p".to_string(), pid_filter.to_string()]);
    }
    if let Some(duration_filter) = duration {
        args.extend(["-d".to_string(), duration_filter.to_string()]);
    }
    let effective_mode = mode.or_else(|| (comm.is_some() || pid.is_some()).then_some(FILTER_MODE));
    if let Some(mode_filter) = effective_mode {
        args.extend(["-m".to_string(), mode_filter.to_string()]);
    }
    if verbose {
        // Diagnostic opt-in: print USER_READ_FAILED notices (pid/comm) to
        // stderr so a real run can attribute unavoidable path-read failures.
        args.push("-v".to_string());
    }
    args
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pid_set_wins_and_empty_set_falls_back() {
        assert_eq!(resolve_pid_targets(Some(9), &[1, 2]), vec![1, 2]);
        assert_eq!(resolve_pid_targets(Some(9), &[]), vec![9]);
        assert!(resolve_pid_targets(None, &[]).is_empty());
    }

    #[test]
    fn filtered_runs_select_filter_mode_explicitly() {
        assert_eq!(
            build_process_args(Some("claude"), None, None, None, false),
            vec!["-c", "claude", "-m", "2"]
        );
        assert_eq!(
            build_process_args(None, Some(7), None, None, false),
            vec!["-p", "7", "-m", "2"]
        );
        assert!(build_process_args(None, None, None, None, false).is_empty());
        assert_eq!(
            build_process_args(Some("claude"), None, None, Some(0), false),
            vec!["-c", "claude", "-m", "0"]
        );
        assert!(
            build_process_args(None, Some(7), Some(500), None, false)
                .windows(2)
                .any(|w| w == ["-d", "500"])
        );
        assert_eq!(
            build_process_args(Some("claude"), None, None, None, true),
            vec!["-c", "claude", "-m", "2", "-v"]
        );
    }
}
