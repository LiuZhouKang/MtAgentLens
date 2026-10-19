// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Single-source guard for shared vocabulary constants.
//
// The failure this prevents is concrete: `AGENT_COMMS` existed twice — once as
// a `pub const` in `identity/union_find.rs` and once as a function-local
// `const` in `signals/signal_snapshot.rs`. Two copies of "which comms identify
// an agent" can drift, and a drift silently changes whether a process is an
// agent without failing any test that reads only one copy.
//
// The rule, as everywhere: assert that what is inspected exists and is
// non-empty, or the guard passes while checking nothing.

use std::fs;
use std::path::{Path, PathBuf};

/// Recursively collect every `.rs` file under `collector/src`.
fn rust_sources() -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![Path::new(env!("CARGO_MANIFEST_DIR")).join("src")];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
            .flatten()
        {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().is_some_and(|e| e == "rs") {
                out.push(path);
            }
        }
    }
    assert!(
        out.len() > 100,
        "found only {} Rust sources under collector/src — the scan is stale",
        out.len()
    );
    out
}

/// Number of definition sites for `name` with one of the given item keywords.
///
/// Counts occurrences, not files: two definitions in one file (a shadowing
/// `const` in a module, or a function-local copy) are exactly the drift this
/// guard exists to catch. Comment text after `//` is ignored, and the name
/// must end at a token boundary so `AGENT_COMMS_EXTRA` does not count.
fn item_definition_count(keywords: &[&str], name: &str) -> (usize, Vec<PathBuf>) {
    let mut count = 0usize;
    let mut sites = Vec::new();
    for path in rust_sources() {
        let src = fs::read_to_string(&path).unwrap_or_default();
        for line in src.lines() {
            let code = line.split("//").next().unwrap_or("");
            for keyword in keywords {
                let pattern = format!("{keyword} {name}");
                let mut from = 0usize;
                while let Some(offset) = code[from..].find(&pattern) {
                    let start = from + offset;
                    let end = start + pattern.len();
                    let boundary = code[end..]
                        .chars()
                        .next()
                        .is_none_or(|c| !c.is_alphanumeric() && c != '_');
                    if boundary {
                        count += 1;
                        sites.push(path.clone());
                        break;
                    }
                    from = end;
                }
            }
        }
    }
    (count, sites)
}

/// One shared vocabulary, one definition. Re-exporting is fine; copying is not.
#[test]
fn agent_comms_has_exactly_one_definition() {
    let (count, sites) = item_definition_count(&["const", "static"], "AGENT_COMMS");
    assert!(
        count > 0,
        "AGENT_COMMS has no definition — the guard would pass vacuously"
    );
    assert_eq!(
        count, 1,
        "AGENT_COMMS is defined {} times: {sites:?}\n\
         Import it from responsibility::identity::union_find instead of copying it.",
        count
    );

    let source = fs::read_to_string(&sites[0]).unwrap_or_default();
    assert!(
        source.contains(r#"["claude", "claude-code"]"#),
        "the single AGENT_COMMS definition no longer carries the expected vocabulary; \
         update this guard deliberately rather than letting it drift"
    );
}

/// `MatchLevel` used to exist twice, with different variants and different
/// ordinal conventions (`types::MatchLevel` discriminants 5..1, legacy
/// `graph_pattern_matcher` 1..5, `argument_matcher` an explicit 5..1 function).
/// The legacy copy was deleted on 2026-10-18 (ADR 0011); this keeps a second
/// copy from reappearing.
#[test]
fn match_level_has_exactly_one_definition() {
    let (count, sites) = item_definition_count(&["enum"], "MatchLevel");
    assert!(
        count > 0,
        "MatchLevel has no definition — the guard would pass vacuously"
    );
    assert_eq!(
        count, 1,
        "MatchLevel is defined {} times: {sites:?}\n\
         There must be exactly one match-level taxonomy. See docs/adr/0011-one-match-level.md.",
        count
    );
}
