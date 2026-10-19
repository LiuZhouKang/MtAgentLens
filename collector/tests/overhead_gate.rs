// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Offline decode-budget gate (P0.5 deliverable 7).
//!
//! The live collector overhead needs sudo and an LLM endpoint, so it cannot run
//! in `scripts/verify.sh`. What *can* run everywhere is the deterministic part:
//! decoding the frozen wire corpus must stay within a generous throughput and
//! memory budget. A regression that makes the chunk parser quadratic, or that
//! lets an oversized zstd window allocate freely, fails here.

use std::fs;
use std::path::PathBuf;
use std::time::Instant;

use mtagentlens::framework::normalize::decode_message;

const ITERATIONS: usize = 200;
/// Input (compressed + headers) plus output bytes are both counted, so this is
/// an order-of-magnitude guard, not a micro-benchmark. Measured locally at
/// ~810 MiB/s in a debug build; 20 MiB/s keeps a 40x margin on this machine and
/// still catches a quadratic chunk parser or an unbounded allocation.
const MIN_THROUGHPUT_MIB_PER_SECOND: f64 = 20.0;
const MAX_PEAK_RSS_MIB: u64 = 512;

fn fixture_path(relative: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/wire")
        .join(relative)
}

fn peak_rss_mib() -> u64 {
    let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("VmHWM:") {
            let kib: u64 = rest
                .trim()
                .trim_end_matches(" kB")
                .trim()
                .parse()
                .unwrap_or(0);
            return kib / 1024;
        }
    }
    0
}

#[test]
fn frozen_corpus_decodes_within_budget() {
    let fixtures = [
        "openclaw-deepseek-zstd-chunked/response.http",
        "claude-code-deepseek-zstd-chunked/response.http",
        "openclaw-intent-crosscheck/response.http",
    ];
    let inputs: Vec<Vec<u8>> = fixtures
        .iter()
        .map(|name| {
            fs::read(fixture_path(name))
                .unwrap_or_else(|error| panic!("cannot read fixture {name}: {error}"))
        })
        .collect();

    // Warm-up keeps the first-call allocator cost out of the measurement.
    for raw in &inputs {
        decode_message(raw).expect("fixture must decode");
    }

    let started = Instant::now();
    let mut total_bytes: u64 = 0;
    for _ in 0..ITERATIONS {
        for raw in &inputs {
            let decoded = decode_message(raw).expect("fixture must decode");
            assert_eq!(decoded.consumed, raw.len());
            total_bytes += (raw.len() + decoded.body.len()) as u64;
        }
    }
    let elapsed = started.elapsed().as_secs_f64();
    let mib = total_bytes as f64 / (1024.0 * 1024.0);
    let throughput = mib / elapsed;
    let peak = peak_rss_mib();

    println!(
        "decoded {ITERATIONS} x {} fixtures: {mib:.1} MiB in {elapsed:.3}s \
         ({throughput:.1} MiB/s), peak RSS {peak} MiB",
        inputs.len()
    );

    assert!(
        throughput >= MIN_THROUGHPUT_MIB_PER_SECOND,
        "decode throughput {throughput:.2} MiB/s fell below the {MIN_THROUGHPUT_MIB_PER_SECOND} MiB/s budget"
    );
    assert!(
        peak < MAX_PEAK_RSS_MIB,
        "peak RSS {peak} MiB exceeded the {MAX_PEAK_RSS_MIB} MiB budget"
    );
}

#[test]
fn committed_overhead_baseline_is_complete() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("docs/evaluation/overhead-baseline.json");
    let bytes = fs::read(&path).unwrap_or_else(|error| {
        panic!(
            "missing overhead baseline at {}: {error} \
             (run ./scripts/wire/measure-overhead.sh)",
            path.display()
        )
    });
    let baseline: serde_json::Value = serde_json::from_slice(&bytes).unwrap();

    assert_eq!(
        baseline["schema"].as_str(),
        Some("mtagentlens.overhead-baseline/1")
    );
    let collector = &baseline["collector"];
    for field in [
        "binary_sha256",
        "command",
        "cpu_percent_mean",
        "rss_peak_mib",
        "events_per_second",
        "log_bytes_per_second",
    ] {
        assert!(
            !collector[field].is_null(),
            "overhead baseline is missing collector.{field}"
        );
    }
    assert!(
        collector["events_per_second"].as_f64().unwrap_or(0.0) > 0.0,
        "overhead baseline recorded no events; the measurement did not see the workload"
    );
    assert!(
        !baseline["environment"]["kernel"]
            .as_str()
            .unwrap_or("")
            .is_empty(),
        "overhead baseline must record the kernel"
    );
}
