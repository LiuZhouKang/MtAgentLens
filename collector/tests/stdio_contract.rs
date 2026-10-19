// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Contract pins for the stdio capture channel.
//!
//! Two failure modes are invisible at runtime and are therefore pinned at the
//! source level here:
//!   1. stdiocap used to ignore `bpf_probe_read_user`'s return and to drop
//!      ring-buffer reservations silently. A capture channel that hides its own
//!      failures cannot support any claim that "nothing was observed".
//!   2. stdio payloads used to flow into `IntentExtractor` (routing falls back
//!      to `data` for any event with a payload), turning raw file bytes into
//!      phantom tool intents. `route_event` now makes every stdio event
//!      terminal before dispatch.

use std::fs;
use std::path::PathBuf;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("collector has a parent")
        .to_path_buf()
}

fn read(relative: &str) -> String {
    fs::read_to_string(repo().join(relative))
        .unwrap_or_else(|error| panic!("cannot read {relative}: {error}"))
}

#[test]
fn stdiocap_reports_copy_failures_and_ring_drops() {
    let header = read("bpftools/stdiocap/stdiocap.h");
    assert!(
        header.contains("copy_failed"),
        "the event struct must carry copy_failed"
    );

    let bpf = read("bpftools/stdiocap/stdiocap.bpf.c");
    for needle in [
        "dropped_events",
        "inc_dropped",
        "copy_failed = 1",
        "event->buf_size = 0",
    ] {
        assert!(bpf.contains(needle), "stdiocap.bpf.c missing {needle}");
    }

    let user = read("bpftools/stdiocap/stdiocap.c");
    for needle in ["copy_failed", "STATS", "dropped_events"] {
        assert!(user.contains(needle), "stdiocap.c missing {needle}");
    }
}

#[test]
fn stdio_events_are_terminal_for_intent_routing() {
    let routing = read("collector/src/responsibility/runtime/routing.rs");
    assert!(
        routing.contains("pub fn is_terminal_runtime_source"),
        "routing must expose the terminal-source policy"
    );
    let runtime = read("collector/src/responsibility/runtime/runtime.rs");
    assert!(
        runtime.contains("is_terminal_runtime_source"),
        "route_event must apply the terminal-source policy"
    );
}
