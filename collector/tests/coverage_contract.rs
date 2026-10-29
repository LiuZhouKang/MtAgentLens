// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Event-type coverage contract.
//
// The router in `responsibility/runtime/routing.rs` matches a set of event type
// strings. The eBPF programs emit a set of event type strings. Until this test
// existed, nothing compared the two, and the result was that the router matched
// **seven** types that no eBPF program has ever emitted:
//
//     FILE_WRITE  FILE_DELETE  FILE_TRUNCATE  FILE_RENAME
//     NETWORK_CONNECT  NETWORK_SEND  PROCESS_EXEC
//
// Nothing failed, nothing warned, and the action layer silently could not see
// network egress or file writes/deletes. That is the same failure mode as the 13
// files that were never compiled: a declaration with no producer, and no machine
// able to notice.
//
// This test makes the gap a build-time fact. It does not demand that the gap be
// closed — closing it means new `fentry` hooks on VFS/network kernel functions,
// which is real work — it demands that the gap be *declared*.
//
// Two rules, as everywhere in this repository:
//  1. Assert the things you inspect exist and are non-empty, or the test passes
//     while checking nothing.
//  2. Read the source of truth from the tree; do not hardcode a second copy.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("collector/ must have a parent directory")
        .to_path_buf()
}

/// Event-type strings the router matches on, read from the router itself.
///
/// This used to filter every uppercase token against a hardcoded allowlist,
/// so a NEW event type added to the router was silently dropped from the
/// contract. Now the allowlist is a checked classification: every uppercase
/// token in production code must be either a known event type or a declared
/// non-event token.
fn router_expected_types() -> BTreeSet<String> {
    let path = repo_root().join("collector/src/responsibility/runtime/routing.rs");
    let src =
        fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
    assert!(
        !src.is_empty(),
        "{} is empty — this test would otherwise pass vacuously",
        path.display()
    );

    // Only production code: the cfg(test) module constructs its own event
    // literals and must not define the contract.
    let body = match src.find("mod tests") {
        Some(index) => &src[..index],
        None => &src[..],
    };
    for marker in ["Some(\"EXEC\") | Some(\"EXIT\")", "| \"NETWORK_SEND\""] {
        assert!(
            body.contains(marker),
            "the router no longer contains {marker:?}; this test's extraction is stale and \
             must be updated, because a stale scan passes while checking nothing"
        );
    }

    let known: BTreeSet<&str> = [
        "EXEC",
        "EXIT",
        "PROCESS_SNAPSHOT",
        "FILE_OPEN",
        "FILE_WRITE",
        "FILE_DELETE",
        "FILE_TRUNCATE",
        "FILE_RENAME",
        "NETWORK_CONNECT",
        "NETWORK_SEND",
        "PROCESS_EXEC",
        "TOOL_CALL",
    ]
    .into_iter()
    .collect();
    // Uppercase strings that are data, not event types. EXEC/EXIT used to be
    // excluded by requiring an underscore, which meant a missing producer for
    // them was invisible to this contract.
    let non_event: BTreeSet<&str> = ["AF_INET", "AF_INET6"].into_iter().collect();

    let mut found = BTreeSet::new();
    for cap in body.split('"').enumerate() {
        if cap.0 % 2 == 1 {
            let token = cap.1;
            if token.len() >= 3
                && token.chars().all(|c| c.is_ascii_uppercase() || c == '_')
                && token.chars().any(|c| c.is_ascii_alphabetic())
            {
                found.insert(token.to_string());
            }
        }
    }

    let unclassified: Vec<&String> = found
        .iter()
        .filter(|token| !known.contains(token.as_str()) && !non_event.contains(token.as_str()))
        .collect();
    assert!(
        unclassified.is_empty(),
        "routing.rs contains uppercase token(s) that this contract does not classify: \
         {unclassified:?}\n\
         If it is an event type, add it to `known` (and give it a producer or declare it \
         in `unbacked_event_types`). If it is data, add it to `non_event` with a reason."
    );

    found
        .into_iter()
        .filter(|token| known.contains(token.as_str()))
        .collect()
}

/// Event-type strings any eBPF program actually emits, read from the C sources.
///
/// Only the three directories the Makefile builds (`BPF_PROGS`) are scanned.
/// Walking all of `bpftools` counted `process_ext/` — a dormant tree with no
/// build target — and `process_ext/map_flush.h` is included only by a test, so
/// deleting a real emitter left this contract green.
fn ebpf_emitted_types() -> BTreeSet<String> {
    let root = repo_root().join("bpftools");
    let mut out = BTreeSet::new();
    for program in ["tls_capture", "process", "stdiocap"] {
        let dir = root.join(program);
        assert!(
            dir.is_dir(),
            "built eBPF program directory {} is missing; the scan is stale",
            dir.display()
        );
        let mut stack = vec![dir];
        while let Some(dir) = stack.pop() {
            for entry in fs::read_dir(&dir)
                .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
                .flatten()
            {
                let path = entry.path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|e| e == "c" || e == "h") {
                    let src = fs::read_to_string(&path).unwrap_or_default();
                    // Emitters that inline the type: `printf("\"event\":\"NAME\"...")`.
                    for (i, chunk) in src.split("\\\"event\\\":\\\"").enumerate().skip(1) {
                        let name: String = chunk
                            .chars()
                            .take_while(|c| c.is_ascii_uppercase() || *c == '_')
                            .collect();
                        if !name.is_empty() {
                            out.insert(name);
                        }
                        let _ = i;
                    }
                    // Emitters that centralise the name in a helper:
                    // `return "NETWORK_SEND";`. Only real returns count — a
                    // mention in a comment is not a producer.
                    for chunk in src.split("return \"").skip(1) {
                        let name: String = chunk
                            .chars()
                            .take_while(|c| c.is_ascii_uppercase() || *c == '_')
                            .collect();
                        if name.len() >= 4 && name.contains('_') {
                            out.insert(name);
                        }
                    }
                }
            }
        }
    }
    out
}

/// Event-type strings the Rust runners emit (not every fact comes from eBPF).
///
/// `TOOL_CALL` is produced by `framework/runners/outofband.rs` from the
/// agent's own trajectory. Scanning only the C sources made a Rust producer
/// look like an undeclared gap.
fn rust_emitted_types() -> BTreeSet<String> {
    let dir = repo_root().join("collector/src/framework/runners");
    assert!(
        dir.is_dir(),
        "runner directory {} is missing; the Rust emitter scan is stale",
        dir.display()
    );
    let mut out = BTreeSet::new();
    for entry in fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
        .flatten()
    {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }
        let src = fs::read_to_string(&path).unwrap_or_default();
        for chunk in src.split("\"event\"").skip(1) {
            let Some(start) = chunk.find('"') else {
                continue;
            };
            let name: String = chunk[start + 1..]
                .chars()
                .take_while(|c| c.is_ascii_uppercase() || *c == '_')
                .collect();
            if name.len() >= 4 && name.contains('_') {
                out.insert(name);
            }
        }
    }
    out
}

fn declared_unbacked() -> BTreeSet<String> {
    let path = repo_root().join("docs/capabilities.json");
    let raw =
        fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
    let caps: serde_json::Value =
        serde_json::from_str(&raw).expect("docs/capabilities.json must be valid JSON");
    // A structured field, not a scan of prose: the previous implementation
    // collected every UPPER_CASE token from known_limitations, so a sentence
    // *describing a new producer* counted as a declaration that no producer
    // exists. The declaration and the evidence must be different things.
    let declared = caps
        .get("unbacked_event_types")
        .and_then(|v| v.as_array())
        .expect(
            "docs/capabilities.json must carry `unbacked_event_types` (an array); \
             without it the gap can only be declared in prose, which cannot be checked",
        );
    declared
        .iter()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect()
}

/// Router event names that are accepted aliases of a produced type.
///
/// `PROCESS_EXEC` is matched by the router but the eBPF layer emits `EXEC`;
/// the alias exists so older inputs keep routing. It is neither backed (it is
/// not emitted) nor a gap (it resolves to a produced type), so it belongs in
/// its own structured field.
fn declared_aliases() -> std::collections::BTreeMap<String, String> {
    let path = repo_root().join("docs/capabilities.json");
    let raw =
        fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
    let caps: serde_json::Value =
        serde_json::from_str(&raw).expect("docs/capabilities.json must be valid JSON");
    let aliases = caps
        .get("event_type_aliases")
        .and_then(|v| v.as_object())
        .expect("docs/capabilities.json must carry `event_type_aliases` (an object)");
    aliases
        .iter()
        .filter_map(|(k, v)| v.as_str().map(|target| (k.clone(), target.to_owned())))
        .collect()
}

#[test]
fn both_sides_of_the_contract_are_discoverable() {
    let expected = router_expected_types();
    let emitted = ebpf_emitted_types();

    assert!(
        expected.len() >= 8,
        "the router scan found only {} event types ({expected:?}) — the extraction is stale, \
         and a stale scan passes while checking nothing",
        expected.len()
    );
    let emitted = emitted
        .union(&rust_emitted_types())
        .cloned()
        .collect::<BTreeSet<_>>();
    assert!(
        emitted.len() >= 3,
        "the producer scan found only {} emitted event types ({emitted:?}) — the extraction is stale",
        emitted.len()
    );
    assert!(
        emitted.contains("EXEC") && emitted.contains("FILE_OPEN"),
        "the eBPF scan did not find known-emitted types; it is broken: {emitted:?}"
    );
}

#[test]
fn every_expected_event_type_has_a_producer_or_is_declared_unbacked() {
    let expected = router_expected_types();
    let emitted: BTreeSet<String> = ebpf_emitted_types()
        .union(&rust_emitted_types())
        .cloned()
        .collect();
    let declared = declared_unbacked();
    let aliases = declared_aliases();

    let unbacked: Vec<&String> = expected
        .iter()
        .filter(|t| {
            let produced = emitted.contains(*t)
                || aliases
                    .get(*t)
                    .is_some_and(|target| emitted.contains(target));
            !produced && !declared.contains(*t)
        })
        .collect();

    assert!(
        unbacked.is_empty(),
        "the router matches event types that no eBPF program emits, and they are not \
         declared in docs/capabilities.json's `unbacked_event_types`: {unbacked:?}\n\
         Either add the producer (fentry on VFS/network kernel functions — note that \
         syscall tracepoints are blind to openat2 and io_uring) or declare the gap. \
         Silence is what let this go unnoticed."
    );
}

/// A declaration is a budget: it may only shrink. If a producer lands, the
/// entry must be removed the same day, or the list becomes a permanent excuse.
#[test]
fn a_declared_gap_must_still_be_a_gap() {
    let emitted = ebpf_emitted_types();
    let declared = declared_unbacked();
    let now_backed: Vec<&String> = declared.iter().filter(|t| emitted.contains(*t)).collect();

    assert!(
        now_backed.is_empty(),
        "these event types are declared in `unbacked_event_types` but an eBPF program \
         now emits them: {now_backed:?}\n\
         Remove them from docs/capabilities.json — the declaration list may only shrink."
    );
}

/// An alias is only honest if both sides are router event types and the
/// target really is produced. Without the first check a declaration such as
/// `"FILE_WRITE": "AF_INET"` would pass (the emitted set contains non-event
/// tokens) and silently drop FILE_WRITE from the budget.
#[test]
fn aliases_resolve_to_produced_types() {
    let emitted = ebpf_emitted_types();
    let expected = router_expected_types();
    for (alias, target) in declared_aliases() {
        assert!(
            expected.contains(&alias),
            "alias `{alias}` is not an event type the router matches; an alias must \
             not introduce a new name"
        );
        assert!(
            expected.contains(&target),
            "alias `{alias}` targets `{target}`, which is not a router event type"
        );
        assert!(
            emitted.contains(&target),
            "docs/capabilities.json declares `{alias}` as an alias of `{target}`, but no \
             eBPF program emits `{target}`; the alias resolves to a nonexistent producer"
        );
    }
}
