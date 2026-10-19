// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Graph pattern-template contract.
//
// The failure this prevents is the one that was live on 2026-10-18: every
// entry in `default_templates()` required at least one node or edge type that
// the graph builder never produces, so the matcher ran on every closure and
// could not return a single match. Nothing failed; the templates looked like
// detection.
//
// The rule is the same as the event-type contract: a requirement must have a
// producer, or it must be declared in `docs/capabilities.json`. Declared gaps
// are a budget — when a producer lands, the declaration must be removed, so
// the list can only shrink.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("collector/ must have a parent directory")
        .to_path_buf()
}

/// First-party Rust sources with their `#[cfg(test)] mod tests` tail removed.
fn production_sources() -> Vec<(PathBuf, String)> {
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
                let src = fs::read_to_string(&path).unwrap_or_default();
                let body = match src.find("mod tests") {
                    Some(index) => src[..index].to_string(),
                    None => src,
                };
                out.push((path, body));
            }
        }
    }
    assert!(
        out.len() > 100,
        "found only {} sources — the scan is stale",
        out.len()
    );
    out
}

/// Names constructed as `edge_type: ResponsibilityEdgeType::X`.
///
/// `pattern_matcher.rs` is excluded: its templates *name* types, they do not
/// construct edges. Counting them as producers is exactly the false green this
/// test exists to prevent.
fn produced_edge_types() -> BTreeSet<String> {
    produced("edge_type: ResponsibilityEdgeType::")
}

/// Names constructed as `node_type: ResponsibilityNodeType::X`.
fn produced_node_types() -> BTreeSet<String> {
    produced("node_type: ResponsibilityNodeType::")
}

fn produced(marker: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    for (path, src) in production_sources() {
        if path.file_name().is_some_and(|n| n == "pattern_matcher.rs") {
            continue;
        }
        for (index, _) in src.match_indices(marker) {
            let rest = &src[index + marker.len()..];
            let name: String = rest
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            if !name.is_empty() {
                out.insert(name);
            }
        }
    }
    out
}

fn capabilities() -> serde_json::Value {
    let path = repo_root().join("docs/capabilities.json");
    let raw =
        fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
    serde_json::from_str(&raw).expect("docs/capabilities.json must be valid JSON")
}

fn declared_unbacked(kind: &str) -> BTreeSet<String> {
    capabilities()["unbacked_graph_types"][kind]
        .as_array()
        .unwrap_or_else(|| {
            panic!("docs/capabilities.json must carry `unbacked_graph_types.{kind}` (an array)")
        })
        .iter()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect()
}

fn declared_inert_templates() -> BTreeSet<String> {
    capabilities()["inert_graph_templates"]
        .as_array()
        .expect("docs/capabilities.json must carry `inert_graph_templates` (an array)")
        .iter()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect()
}

/// Resource kinds the graph builder actually emits.
///
/// A curated set with a source assertion: `NormalizedResource::<Kind>` must
/// appear in `graph_builder.rs` for each entry. The builder is the only
/// producer of graph resources, so a name that disappears there is a stale
/// entry, not a producer.
fn produced_resource_kinds() -> BTreeSet<String> {
    let path = repo_root().join("collector/src/responsibility/graph/graph_builder.rs");
    let src =
        fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
    let body = match src.find("mod tests") {
        Some(index) => &src[..index],
        None => &src[..],
    };

    let kinds = ["File", "Network", "Process", "ToolCall", "Credential"];
    let mut found = BTreeSet::new();
    for kind in kinds {
        // Constructor forms only. A bare `NormalizedResource::<Kind>` also
        // appears in match arms (e.g. the legacy resource-id conversion), so
        // counting those would keep the scan green after every constructor
        // was removed.
        let constructed = body.contains(&format!("Some(NormalizedResource::{kind}"))
            || body.contains(&format!("&NormalizedResource::{kind}"));
        if constructed {
            found.insert(kind.to_string());
        }
    }
    assert!(
        found.len() >= 5,
        "the resource-kind scan found only {found:?}; graph_builder no longer \
         constructs the expected resource kinds and this contract is stale"
    );
    found
}

/// The matcher's own reachability rule, mirrored here.
///
/// Checking only "every required type has a producer" was not enough: a
/// template also fails permanently when `min_matches` exceeds the number of
/// required edge types (`reconnaissance` requires one edge type but needs
/// three matches), or when it needs two agents and no cross-agent edge type is
/// produced. A template that is inert for those reasons must be declared too.
fn matcher_reachable(
    template: &mtagentlens::responsibility::graph::GraphPatternTemplate,
    produced_nodes: &BTreeSet<String>,
    produced_edges: &BTreeSet<String>,
    produced_kinds: &BTreeSet<String>,
) -> bool {
    use mtagentlens::responsibility::types::graph_types::ResponsibilityEdgeType;

    let nodes_ok = template
        .required_node_types
        .iter()
        .all(|node| produced_nodes.contains(&format!("{node:?}")));

    let kinds_ok = template
        .required_resource_kinds
        .iter()
        .all(|kind| produced_kinds.contains(&format!("{kind:?}")));

    let found_edges = template
        .required_edge_types
        .iter()
        .filter(|edge| produced_edges.contains(&format!("{edge:?}")))
        .count();
    let edges_ok = template.required_edge_types.is_empty() || found_edges >= template.min_matches;

    let cross_agent_ok = if template.min_distinct_agents >= 2 {
        [
            ResponsibilityEdgeType::Delegation,
            ResponsibilityEdgeType::AgentDelegatesTo,
            ResponsibilityEdgeType::ContextInheritance,
            ResponsibilityEdgeType::ContextFlowsFrom,
            ResponsibilityEdgeType::TaintPropagation,
            ResponsibilityEdgeType::TaintPropagatesTo,
        ]
        .iter()
        .any(|edge| produced_edges.contains(&format!("{edge:?}")))
    } else {
        true
    };

    nodes_ok && kinds_ok && edges_ok && cross_agent_ok
}

#[test]
fn the_producer_scan_is_not_vacuous() {
    let edges = produced_edge_types();
    let nodes = produced_node_types();

    for expected in [
        "IntentCausesAction",
        "PolicyBoundaryCrossing",
        "TaintPropagatesTo",
        "AgentDelegatesTo",
        "ContextFlowsFrom",
    ] {
        assert!(
            edges.contains(expected),
            "the edge scan did not find `{expected}`; it is stale. Found: {edges:?}"
        );
    }
    for expected in ["Agent", "Resource", "PolicyBoundary"] {
        assert!(
            nodes.contains(expected),
            "the node scan did not find `{expected}`; it is stale. Found: {nodes:?}"
        );
    }
}

#[test]
fn every_template_requirement_is_produced_or_declared_unbacked() {
    use mtagentlens::responsibility::graph::default_templates;

    let edges = produced_edge_types();
    let nodes = produced_node_types();
    let kinds = produced_resource_kinds();
    let unbacked_edges = declared_unbacked("edge");
    let unbacked_nodes = declared_unbacked("node");
    let unbacked_kinds = declared_unbacked("resource_kinds");

    let templates = default_templates();
    assert!(
        templates.len() >= 10,
        "only {} default templates found — the scan is stale",
        templates.len()
    );

    let mut undeclared = Vec::new();
    for template in &templates {
        for edge in &template.required_edge_types {
            let name = format!("{edge:?}");
            if !edges.contains(&name) && !unbacked_edges.contains(&name) {
                undeclared.push(format!("{} requires edge {name}", template.template_id));
            }
        }
        for node in &template.required_node_types {
            let name = format!("{node:?}");
            if !nodes.contains(&name) && !unbacked_nodes.contains(&name) {
                undeclared.push(format!("{} requires node {name}", template.template_id));
            }
        }
        for kind in &template.required_resource_kinds {
            let name = format!("{kind:?}");
            if !kinds.contains(&name) && !unbacked_kinds.contains(&name) {
                undeclared.push(format!(
                    "{} requires resource kind {name}",
                    template.template_id
                ));
            }
        }
        for (edge_type, kind) in &template.required_edge_target_kinds {
            let edge_name = format!("{edge_type:?}");
            let kind_name = format!("{kind:?}");
            if !template
                .required_edge_types
                .iter()
                .any(|edge| format!("{edge:?}") == edge_name)
            {
                undeclared.push(format!(
                    "{} constrains the target of {edge_name}, which is not in its \
                     required_edge_types",
                    template.template_id
                ));
            }
            if !kinds.contains(&kind_name) && !unbacked_kinds.contains(&kind_name) {
                undeclared.push(format!(
                    "{} requires edge-target kind {kind_name}",
                    template.template_id
                ));
            }
        }
    }

    assert!(
        undeclared.is_empty(),
        "these template requirements have no producer and are not declared in \
         docs/capabilities.json's `unbacked_graph_types`: {undeclared:#?}\n\
         Either add the producer (graph_builder must construct the type) or declare \
         the gap. An undeclared requirement is a matcher that silently cannot fire."
    );
}

/// A declaration is a budget: it may only shrink.
#[test]
fn a_declared_unbacked_graph_type_must_not_have_a_producer() {
    let edges = produced_edge_types();
    let nodes = produced_node_types();
    let kinds = produced_resource_kinds();
    let declared_edges = declared_unbacked("edge");
    let declared_nodes = declared_unbacked("node");
    let declared_kinds = declared_unbacked("resource_kinds");

    let backed_edges: Vec<&String> = declared_edges
        .iter()
        .filter(|t| edges.contains(*t))
        .collect();
    let backed_nodes: Vec<&String> = declared_nodes
        .iter()
        .filter(|t| nodes.contains(*t))
        .collect();
    let backed_kinds: Vec<&String> = declared_kinds
        .iter()
        .filter(|t| kinds.contains(*t))
        .collect();

    assert!(
        backed_edges.is_empty() && backed_nodes.is_empty() && backed_kinds.is_empty(),
        "declared unbacked but now produced — remove from docs/capabilities.json:\n\
         edges: {backed_edges:?}\n nodes: {backed_nodes:?}\n kinds: {backed_kinds:?}"
    );
}

/// `min_matches` counts distinct required edge types, not occurrences. A
/// template that asks for more matches than it has edge types can never
/// satisfy the matcher, even after every producer lands.
#[test]
fn no_template_requires_more_matches_than_it_has_edge_types() {
    use mtagentlens::responsibility::graph::default_templates;

    for template in default_templates() {
        if template.required_edge_types.is_empty() {
            continue;
        }
        assert!(
            template.min_matches <= template.required_edge_types.len(),
            "template `{}` requires {} matches but names only {} edge type(s); \
             the matcher counts distinct types, so this is unreachable",
            template.template_id,
            template.min_matches,
            template.required_edge_types.len()
        );
    }
}

/// Every template that requires an unbacked type is inert today. Listing them
/// makes the gap reviewable; the list may only shrink as producers land.
#[test]
fn inert_templates_are_declared_exactly() {
    use mtagentlens::responsibility::graph::default_templates;

    let edges = produced_edge_types();
    let nodes = produced_node_types();
    let kinds = produced_resource_kinds();
    let inert: BTreeSet<String> = default_templates()
        .into_iter()
        .filter(|template| !matcher_reachable(template, &nodes, &edges, &kinds))
        .map(|template| template.template_id)
        .collect();

    assert_eq!(
        inert,
        declared_inert_templates(),
        "\nThe set of templates that cannot fire changed.\n\
         Computed inert: {inert:?}\n\
         Declared in docs/capabilities.json: {:?}\n\
         If a template became matchable, remove it from `inert_graph_templates`. \
         If a template became inert, that is a regression — fix the producer.",
        declared_inert_templates()
    );
}
