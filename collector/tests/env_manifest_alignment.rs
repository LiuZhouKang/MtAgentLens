// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Scenario-manifest alignment guard.
//
// This file previously asserted that `docs/design/experiment-design.md` contained
// 17 hardcoded scenario filenames, and that they lived flat at
// `env/scenarios/<name>.json`. Neither is true any more: scenarios are organised
// as `env/scenarios/<category>/<name>.json`, and the doc never listed them. The
// guard therefore failed while checking a layout that no longer exists — and it
// would have *silently passed* had the doc been missing, because it `return`ed
// early in that case.
//
// Two rules, learned from the failures this repository has actually had:
//
//  1. A guard must first assert that what it inspects EXISTS and is non-empty.
//     Otherwise it passes while checking nothing (see
//     `responsibility_naming_guards.rs`, which inspects a directory that no
//     longer exists and `continue`s forever).
//  2. Guards check reality against a machine-readable source of truth, not
//     against a hardcoded list. `docs/capabilities.json` is that source: adding
//     a scenario category without registering it here fails the build, and
//     declaring a category with no scenarios behind it fails it too.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR is .../collector; the test assets live one level up.
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("collector/ must have a parent directory")
        .to_path_buf()
}

fn capabilities() -> serde_json::Value {
    let path = repo_root().join("docs/capabilities.json");
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "cannot read the capability manifest at {}: {e}\n\
             This file is the source of truth for scenario categories and known \
             limitations; the guard does not silently skip when it is absent.",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("docs/capabilities.json must be valid JSON")
}

/// Every scenario JSON on disk, as `(category, path)`, excluding non-scenario dirs.
fn discovered_scenarios() -> Vec<(String, PathBuf)> {
    let manifest = capabilities();
    let excluded: BTreeSet<&str> = manifest["scenarios"]["excluded_dirs"]
        .as_array()
        .expect("scenario exclusions must be declared")
        .iter()
        .map(|value| value.as_str().expect("directory name"))
        .collect();
    assert!(
        excluded.contains("_drafts")
            && excluded.contains("archive")
            && excluded.contains("fixtures")
    );
    let scenarios_root = repo_root().join("env/scenarios");
    assert!(
        scenarios_root.is_dir(),
        "{} is missing — this guard would otherwise pass vacuously",
        scenarios_root.display()
    );

    let mut out = Vec::new();
    let mut stack = vec![scenarios_root.clone()];
    while let Some(dir) = stack.pop() {
        let entries =
            fs::read_dir(&dir).unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()));
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if !excluded.contains(name.as_str()) {
                    stack.push(path);
                }
            } else if path.extension().is_some_and(|e| e == "json") {
                let category = path
                    .strip_prefix(&scenarios_root)
                    .ok()
                    .and_then(|rel| rel.iter().next())
                    .map(|c| c.to_string_lossy().into_owned())
                    .unwrap_or_default();
                out.push((category, path));
            }
        }
    }
    out
}

#[test]
fn capabilities_manifest_is_present_and_substantive() {
    let caps = capabilities();
    let limitations = caps["known_limitations"]
        .as_array()
        .expect("known_limitations must be an array");
    assert!(
        !limitations.is_empty(),
        "a capability manifest with no known limitations is not a manifest, it is marketing"
    );
    assert!(
        caps["kernel_requirement"]["minimum"].as_str() == Some("5.8"),
        "the kernel floor must be declared once, here, and match the eBPF programs"
    );
}

#[test]
fn discovered_scenarios_are_real_and_non_empty() {
    let scenarios = discovered_scenarios();
    assert_eq!(
        Some(scenarios.len() as u64),
        capabilities()["scenarios"]["active_scenario_files"].as_u64(),
        "the active file count must follow the same declared scope as category discovery"
    );
    // The point of this assertion is to make the guard non-vacuous: if the
    // layout changes again, this fails loudly instead of checking nothing.
    assert!(
        scenarios.len() > 50,
        "expected the repository to hold many scenario files, found {} — the layout \
         probably moved, which would make the alignment assertions below meaningless",
        scenarios.len()
    );

    for (category, path) in &scenarios {
        let raw = fs::read_to_string(path)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
        let value: serde_json::Value = serde_json::from_str(&raw)
            .unwrap_or_else(|e| panic!("{} is not valid JSON: {e}", path.display()));
        assert!(
            value
                .get("id")
                .and_then(|v| v.as_str())
                .is_some_and(|s| !s.is_empty()),
            "{} has no non-empty `id`",
            path.display()
        );
        assert!(
            !category.is_empty(),
            "{} is not inside a category directory",
            path.display()
        );
    }
}

#[test]
fn every_scenario_category_is_declared_in_the_manifest() {
    let declared: BTreeSet<String> = capabilities()["scenarios"]["categories"]
        .as_array()
        .expect("scenarios.categories must be an array")
        .iter()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect();
    assert!(
        !declared.is_empty(),
        "the manifest declares no scenario categories"
    );

    let on_disk: BTreeSet<String> = discovered_scenarios()
        .into_iter()
        .map(|(category, _)| category)
        .collect();

    let undeclared: Vec<&String> = on_disk.difference(&declared).collect();
    assert!(
        undeclared.is_empty(),
        "these scenario categories exist on disk but are not declared in \
         docs/capabilities.json: {undeclared:?}\n\
         Register them (the manifest is the source of truth) rather than deleting them."
    );

    let phantom: Vec<&String> = declared.difference(&on_disk).collect();
    assert!(
        phantom.is_empty(),
        "docs/capabilities.json declares scenario categories that have no scenarios \
         behind them: {phantom:?}\n\
         A declared-but-absent capability is exactly the failure mode this manifest exists \
         to prevent."
    );
}
