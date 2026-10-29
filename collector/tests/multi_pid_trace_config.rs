// SPDX-License-Identifier: Apache-2.0 OR MIT
//! The multi-PID trace configuration must be the production helper.
//!
//! This file used to define private copies of `build_process_args_for_pid` and
//! `build_system_targets`; changing the collector left the test green. It now
//! imports the library functions that `main.rs` uses.

use mtagentlens::framework::runners::targets::{build_process_args, resolve_pid_targets};

#[test]
fn multi_pid_process_args_expand_per_target_pid() {
    let pid_set = [1111, 2222, 3333];

    let per_pid_args: Vec<Vec<String>> = pid_set
        .iter()
        .map(|pid| build_process_args(Some("claude"), Some(*pid), None, None, false))
        .collect();

    assert_eq!(per_pid_args.len(), 3);
    for (args, pid) in per_pid_args.iter().zip([1111, 2222, 3333]) {
        assert!(args.windows(2).any(|w| w == ["-c", "claude"]), "{args:?}");
        assert!(
            args.windows(2).any(|w| w == ["-p", &pid.to_string()]),
            "{args:?}"
        );
        // A filtered run must select FILTER mode explicitly.
        assert!(args.windows(2).any(|w| w == ["-m", "2"]), "{args:?}");
        // Flags the helper does not support must never be passed.
        assert!(!args.iter().any(|a| a.starts_with("--trace-")), "{args:?}");
    }
}

#[test]
fn multi_pid_system_targets_prefer_pid_set_over_single_pid() {
    assert_eq!(
        resolve_pid_targets(Some(9999), &[1111, 2222]),
        vec![1111, 2222]
    );
}

#[test]
fn multi_pid_system_targets_fall_back_to_single_pid() {
    assert_eq!(resolve_pid_targets(Some(9999), &[]), vec![9999]);
    assert!(resolve_pid_targets(None, &[]).is_empty());
}

#[test]
fn unfiltered_process_args_carry_no_mode_or_filters() {
    let args = build_process_args(None, None, None, None, false);
    assert!(args.is_empty(), "{args:?}");
    assert!(!args.iter().any(|a| a == "-m"), "{args:?}");
}
