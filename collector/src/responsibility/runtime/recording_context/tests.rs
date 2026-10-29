// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use super::*;
use serde_json::json;
use std::fs;
use std::os::unix::fs::symlink;

fn fixture(root: &Path) {
    let own = root.join("101");
    fs::create_dir_all(own.join("ns")).unwrap();
    symlink("101", root.join("self")).unwrap();
    let fields: Vec<String> = (3..=52)
        .map(|i| {
            if i == 3 {
                "S".into()
            } else if i == 22 {
                "777".into()
            } else {
                "0".into()
            }
        })
        .collect();
    fs::write(
        own.join("stat"),
        format!("101 (name with ) parentheses) {}", fields.join(" ")),
    )
    .unwrap();
    fs::write(own.join("status"), "NSpid:\t101\n").unwrap();
    fs::write(
        own.join("timens_offsets"),
        "monotonic -2 10\nboottime 3 20\n",
    )
    .unwrap();
    for name in [
        "pid",
        "pid_for_children",
        "time",
        "time_for_children",
        "mnt",
    ] {
        fs::write(own.join("ns").join(name), "").unwrap();
    }
    fs::create_dir_all(root.join("sys/kernel/random")).unwrap();
    fs::write(
        root.join("sys/kernel/random/boot_id"),
        "d32c240e-0fd1-45e0-83bf-7cb2d2a39d32\n",
    )
    .unwrap();
}

#[test]
fn captures_exact_self_context_without_converting_start_ticks() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let sample = capture_from(dir.path(), 101);
    assert_eq!(sample["schema"], "mtagentlens.collector-process/v1");
    assert_eq!(sample["pid_space_status"], "observed");
    assert_eq!(sample["pid"], 101);
    assert_eq!(sample["procfs_pid"], 101);
    assert_eq!(sample["procfs_start_ticks"], 777);
    assert_eq!(sample["nspid"], json!([101]));
    assert_eq!(sample["boot_id"], "d32c240e-0fd1-45e0-83bf-7cb2d2a39d32");
    assert_eq!(
        sample["time_offsets_ns"],
        json!({"monotonic": -1999999990_i64,"boottime":3000000020_i64})
    );
    for name in [
        "pid",
        "pid_for_children",
        "time",
        "time_for_children",
        "mnt",
    ] {
        assert!(sample["namespaces"][name]["inode"].as_u64().unwrap() > 0);
        assert!(sample["namespaces"][name]["device"].is_u64());
    }
}

#[test]
fn namespace_pid_mismatch_preserves_raw_self_identity_without_claiming_host_pid() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let sample = capture_from(dir.path(), 1);
    assert_eq!(sample["pid_space_status"], "pid_space_mismatch");
    assert_eq!(sample["pid"], 1);
    assert_eq!(sample["procfs_pid"], 101);
    assert_eq!(sample["procfs_start_ticks"], 777);
}

#[test]
fn nested_nspid_cannot_be_claimed_as_matching_procfs_space() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    fs::write(dir.path().join("101/status"), "NSpid:\t999 101\n").unwrap();
    assert_eq!(
        capture_from(dir.path(), 101)["pid_space_status"],
        "pid_space_mismatch"
    );
}

#[test]
fn absent_proc_and_kernel_features_remain_explicitly_unknown() {
    let dir = tempfile::tempdir().unwrap();
    let sample = capture_from(dir.path(), 101);
    assert_eq!(sample["pid_space_status"], "unavailable");
    for key in ["procfs_pid", "procfs_start_ticks", "nspid", "boot_id"] {
        assert!(sample[key].is_null());
    }
    assert!(sample["namespaces"]["time"].is_null());
    assert!(sample["time_offsets_ns"]["monotonic"].is_null());
}

#[test]
fn malformed_stat_and_oversized_status_never_become_complete_identity() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    fs::write(dir.path().join("101/stat"), "malformed").unwrap();
    fs::write(dir.path().join("101/status"), "x".repeat(1_048_577)).unwrap();
    let sample = capture_from(dir.path(), 101);
    assert_eq!(sample["pid_space_status"], "unavailable");
    assert!(sample["procfs_start_ticks"].is_null());
    assert!(sample["nspid"].is_null());
}

#[test]
fn invalid_offsets_overflow_and_duplicate_clocks_are_unknown() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    for raw in [
        "monotonic 0 1000000000\n",
        "monotonic 9223372036854775807 0\n",
        "monotonic 0 0\nmonotonic 1 0\n",
    ] {
        fs::write(dir.path().join("101/timens_offsets"), raw).unwrap();
        assert!(capture_from(dir.path(), 101)["time_offsets_ns"]["monotonic"].is_null());
    }
}

#[test]
fn changing_procfs_self_number_is_not_accepted_as_observed() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    fs::remove_file(dir.path().join("self")).unwrap();
    symlink("999", dir.path().join("self")).unwrap();
    assert_ne!(
        capture_from(dir.path(), 101)["pid_space_status"],
        "observed"
    );
}

#[test]
fn real_self_context_is_measured_and_serializable() {
    let sample = capture();
    assert_eq!(sample["pid"], std::process::id());
    assert_eq!(sample["pid_space_status"], "observed");
    assert!(sample["procfs_start_ticks"].as_u64().unwrap() > 0);
    assert!(serde_json::to_string(&sample).is_ok());
}

#[test]
fn conflicting_stat_pid_does_not_attach_start_ticks_to_the_self_link_pid() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let path = dir.path().join("101/stat");
    let raw = fs::read_to_string(&path)
        .unwrap()
        .replacen("101 (", "202 (", 1);
    fs::write(path, raw).unwrap();
    let sample = capture_from(dir.path(), 101);
    assert_eq!(sample["pid_space_status"], "unavailable");
    assert!(sample["procfs_start_ticks"].is_null());
}
