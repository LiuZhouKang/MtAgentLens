// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Retirement guard for the causal-link vocabulary.
//
// The previous version of this file listed 41 paths under
// `src/accountability/` and used a `read_file_if_exists` helper that
// `continue`d on a missing file. When that tree was deleted with the causal
// schema (ADR 0005), every path was skipped and both tests passed while
// checking nothing — the cautionary tale in ADR 0001.
//
// A guard must assert that its subject exists. Here the subject is an
// *absence*: the retired tree must not come back, and the live
// `responsibility/` sources must not reintroduce the retired API names.

use std::fs;
use std::path::{Path, PathBuf};

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// API names that belonged to the retired causal-link surface.
///
/// Plain strings such as the retired table name `"causal_links"` are
/// deliberately not listed: `persistence/schema.rs::CAUSAL_MARKERS` must keep
/// naming them to recognise a legacy database and refuse to write DDL. The
/// guard is about code, not about a denylist's data.
const RETIRED_API_TERMS: &[&str] = &[
    "causal_link::",
    "CausalLinkStreaming",
    "CausalLinksAnalyzer",
    "causalLinkIds",
];

#[test]
fn retired_accountability_tree_has_not_returned() {
    let path = manifest_dir().join("src/accountability");
    assert!(
        !path.exists(),
        "{} exists again: the accountability tree was retired with the causal \
         schema (ADR 0005). Reintroducing it needs an ADR, not a directory.",
        path.display()
    );
}

#[test]
fn live_responsibility_sources_do_not_reintroduce_the_retired_api() {
    let root = manifest_dir().join("src/responsibility");
    let mut scanned = 0usize;
    let mut violations = Vec::new();
    let mut stack = vec![root];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
            .flatten()
        {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().is_some_and(|e| e == "rs") {
                scanned += 1;
                let relative = path
                    .strip_prefix(manifest_dir())
                    .unwrap_or(Path::new(""))
                    .display()
                    .to_string();
                let content = fs::read_to_string(&path).unwrap_or_default();
                for term in RETIRED_API_TERMS {
                    if content.contains(term) {
                        violations.push(format!("{relative} contains {term}"));
                    }
                }
            }
        }
    }

    // Non-vacuous: the live tree is large and must be present.
    assert!(
        scanned > 50,
        "scanned only {scanned} files under src/responsibility — the tree moved, \
         which would make this guard meaningless"
    );
    assert!(violations.is_empty(), "{}", violations.join("\n"));
}
