// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! The collector embeds the eBPF binaries at compile time.
//!
//! A stale embedded binary is a real failure mode in this repo: a C change can
//! be built, the collector rebuilt from an old cached copy, and every runtime
//! test still passes because no capture is performed. A `strings` probe cannot
//! detect this (the Rust source contains the same strings). Comparing the
//! embedded bytes with the file on disk can.

use std::fs;
use std::path::PathBuf;

const EMBEDDED_TLS_CAPTURE: &[u8] = include_bytes!("../../bpftools/.output/bin/tls_capture");
const EMBEDDED_PROCESS: &[u8] = include_bytes!("../../bpftools/.output/bin/process");
const EMBEDDED_STDIOCAP: &[u8] = include_bytes!("../../bpftools/.output/bin/stdiocap");

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("collector has a parent")
        .to_path_buf()
}

#[test]
fn embedded_binaries_match_the_built_files() {
    let cases: [(&str, &[u8]); 3] = [
        ("tls_capture", EMBEDDED_TLS_CAPTURE),
        ("process", EMBEDDED_PROCESS),
        ("stdiocap", EMBEDDED_STDIOCAP),
    ];
    for (name, embedded) in cases {
        let path = repo().join(format!("bpftools/.output/bin/{name}"));
        let disk = fs::read(&path)
            .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
        assert!(!disk.is_empty(), "the built {name} is empty");
        assert_eq!(
            embedded,
            disk.as_slice(),
            "the embedded {name} is stale; rebuild bpftools/.output/bin/{name} \
             before building the collector"
        );
    }
}

#[test]
fn embedded_tls_capture_carries_the_base64_field() {
    // The C producer must contain the literal field name it emits. This is a
    // secondary check; the byte comparison above is the authoritative one.
    let needle = b"data_b64";
    assert!(
        EMBEDDED_TLS_CAPTURE
            .windows(needle.len())
            .any(|window| window == needle),
        "embedded tls_capture does not contain the data_b64 field"
    );
}

#[test]
fn embedded_stdiocap_reports_capture_failures() {
    let needle = b"copy_failed";
    assert!(
        EMBEDDED_STDIOCAP
            .windows(needle.len())
            .any(|window| window == needle),
        "embedded stdiocap does not contain the copy_failed field"
    );
}
