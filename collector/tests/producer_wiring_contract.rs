// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Producer wiring gate: every production `Runner`/`Analyzer` impl must be
//! reachable from the only composition root (`src/main.rs`).
//!
//! Background: `cargo test` used to stay green when a runner or analyzer
//! registration was deleted from `run_trace`, because nothing observed the
//! composition. `docs/capabilities.json` asserted the registration instead of
//! proving it. This test derives the expected sets from the source itself, so
//! a new impl that is never registered fails, and a deleted registration fails
//! naming the type.
//!
//! Scope and limits:
//! - the scan is textual, not type-checked: it reads `impl <Trait> for <Type>`
//!   and the `add_runner(...)` / `add_analyzer(...)` call spans in `main.rs`;
//! - test doubles (`runners/fake.rs`) and anything after a `#[cfg(test)]`
//!   marker are excluded, because they are not production producers;
//! - `AgentRunner` is the composition root, not a registered child, so it is
//!   checked by construction instead of by `add_runner`.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn rust_files(root: &Path, out: &mut Vec<PathBuf>) {
    let entries = fs::read_dir(root).expect("read source directory");
    for entry in entries {
        let path = entry.expect("read source entry").path();
        if path.is_dir() {
            rust_files(&path, out);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            out.push(path);
        }
    }
}

/// Source text before the first `#[cfg(test)] mod ...` block.
///
/// A single `#[cfg(test)]` can also decorate a field, a `use`, or a builder
/// method (as in `runners/outofband.rs`); cutting at those would hide real
/// producers. Only an actual test module marks the end of production code.
fn production_text(path: &Path) -> String {
    let text = fs::read_to_string(path).expect("read source file");
    let lines: Vec<&str> = text.split_inclusive('\n').collect();
    for (index, line) in lines.iter().enumerate() {
        if line.trim() != "#[cfg(test)]" {
            continue;
        }
        for next in lines.iter().skip(index + 1).take(5) {
            let trimmed = next.trim();
            if trimmed.is_empty() || trimmed.starts_with("#[") || trimmed.starts_with("//") {
                continue;
            }
            if trimmed.starts_with("mod ") {
                return lines[..index].concat();
            }
            break;
        }
    }
    text
}

fn production_impls(src: &Path, trait_name: &str) -> BTreeSet<String> {
    let needle = format!("impl {trait_name} for ");
    let mut files = Vec::new();
    rust_files(src, &mut files);
    let mut found = BTreeSet::new();
    for path in files {
        if path.file_name().is_some_and(|name| name == "fake.rs") {
            continue;
        }
        for line in production_text(&path).lines() {
            let Some(rest) = line.trim_start().strip_prefix(needle.as_str()) else {
                continue;
            };
            let name: String = rest
                .chars()
                .take_while(|ch| ch.is_alphanumeric() || *ch == '_')
                .collect();
            if !name.is_empty() {
                found.insert(name);
            }
        }
    }
    found
}

/// Text of every `needle( ... )` call, parentheses balanced.
///
/// The balance scan is naive about parentheses inside string literals; the
/// registration calls in `main.rs` do not contain them.
fn registration_spans(text: &str, needle: &str) -> Vec<String> {
    let mut spans = Vec::new();
    let mut cursor = 0;
    while let Some(offset) = text[cursor..].find(needle) {
        let start = cursor + offset;
        let Some(paren_offset) = text[start..].find('(') else {
            break;
        };
        let open = start + paren_offset;
        let mut depth = 0usize;
        let mut end = None;
        for (index, ch) in text[open..].char_indices() {
            match ch {
                '(' => depth += 1,
                ')' => {
                    depth -= 1;
                    if depth == 0 {
                        end = Some(open + index + 1);
                        break;
                    }
                }
                _ => {}
            }
        }
        match end {
            Some(end) => {
                spans.push(text[start..end].to_string());
                cursor = end;
            }
            None => break,
        }
    }
    spans
}

fn identifiers(text: &str) -> BTreeSet<String> {
    text.split(|ch: char| !(ch.is_alphanumeric() || ch == '_'))
        .filter(|token| !token.is_empty())
        .map(str::to_string)
        .collect()
}

/// `let [mut] name = ... <Type>::...;` bindings in `main.rs`.
fn aliases(main_rs: &str, type_name: &str) -> BTreeSet<String> {
    let lines: Vec<&str> = main_rs.lines().collect();
    let mut found = BTreeSet::new();
    for (index, line) in lines.iter().enumerate() {
        let Some(rest) = line.trim_start().strip_prefix("let ") else {
            continue;
        };
        let rest = rest.strip_prefix("mut ").unwrap_or(rest);
        let name: String = rest
            .chars()
            .take_while(|ch| ch.is_alphanumeric() || *ch == '_')
            .collect();
        if name.is_empty() {
            continue;
        }
        let window: String = lines[index..lines.len().min(index + 6)].join("\n");
        let statement = &window[..window.find(';').unwrap_or(window.len())];
        if statement.contains(&format!("{type_name}::")) {
            found.insert(name);
        }
    }
    found
}

fn wired(type_name: &str, span_idents: &BTreeSet<String>, main_rs: &str) -> bool {
    if span_idents.contains(type_name) {
        return true;
    }
    if aliases(main_rs, type_name)
        .iter()
        .any(|alias| span_idents.contains(alias))
    {
        return true;
    }
    // Factory path: `let x = factory(...)` where `factory` explicitly returns
    // this type, and the same function both makes that call and registers `x`.
    // The same-function requirement keeps a binding in an uncalled function
    // from acting as a global alias.
    span_idents
        .iter()
        .any(|alias| factory_alias_registered(main_rs, type_name, alias))
}

fn declared_function_name(line: &str) -> Option<String> {
    let rest = line.split_once("fn ")?.1;
    let name: String = rest
        .chars()
        .take_while(|ch| ch.is_alphanumeric() || *ch == '_')
        .collect();
    (!name.is_empty()).then_some(name)
}

/// Top-level `(name, function text)` pairs in source order.
///
/// Rustfmt puts top-level items and their closing brace at column zero, so
/// indented nested functions are deliberately not candidates.
fn top_level_functions(text: &str) -> Vec<(String, String)> {
    let lines: Vec<&str> = text.lines().collect();
    let mut functions = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let line = lines[index];
        let starts_item = line == line.trim_start()
            && ["fn ", "async fn ", "pub fn ", "pub async fn "]
                .iter()
                .any(|prefix| line.starts_with(prefix));
        let Some(name) = (starts_item)
            .then(|| declared_function_name(line))
            .flatten()
        else {
            index += 1;
            continue;
        };

        let mut end = index + 1;
        while end < lines.len() && lines[end] != "}" {
            end += 1;
        }
        let end = end.min(lines.len().saturating_sub(1));
        functions.push((name, lines[index..=end].join("\n")));
        index = end + 1;
    }
    functions
}

/// Explicit return type text of a top-level function, before its body `{`.
fn function_return_type(function: &str) -> Option<String> {
    let signature = &function[..function.find('{')?];
    let arrow = signature.find("->")?;
    let return_type = signature[arrow + 2..].trim();
    (!return_type.is_empty()).then(|| return_type.to_string())
}

/// Names of top-level functions whose explicit return type mentions `type_name`.
fn typed_factory_names(text: &str, type_name: &str) -> BTreeSet<String> {
    top_level_functions(text)
        .into_iter()
        .filter_map(|(name, function)| {
            let return_type = function_return_type(&function)?;
            identifiers(&return_type)
                .contains(type_name)
                .then_some(name)
        })
        .collect()
}

/// `let [mut] x = ...factory(...)...;` bindings inside one function body.
fn factory_binding_aliases_in(function: &str, factories: &BTreeSet<String>) -> BTreeSet<String> {
    let lines: Vec<&str> = function.lines().collect();
    let mut found = BTreeSet::new();
    for (index, line) in lines.iter().enumerate() {
        let Some(rest) = line.trim_start().strip_prefix("let ") else {
            continue;
        };
        let rest = rest.strip_prefix("mut ").unwrap_or(rest);
        let name: String = rest
            .chars()
            .take_while(|ch| ch.is_alphanumeric() || *ch == '_')
            .collect();
        if name.is_empty() {
            continue;
        }

        let mut statement = String::new();
        for next in &lines[index..lines.len().min(index + 50)] {
            statement.push_str(next);
            statement.push('\n');
            if next.contains(';') {
                break;
            }
        }
        let compact: String = statement.chars().filter(|ch| !ch.is_whitespace()).collect();
        if factories
            .iter()
            .any(|factory| compact.contains(&format!("{factory}(")))
        {
            found.insert(name);
        }
    }
    found
}

fn registration_idents(function: &str) -> BTreeSet<String> {
    let mut spans = registration_spans(function, "add_runner(");
    spans.extend(registration_spans(function, "add_analyzer("));
    spans.extend(registration_spans(function, "add_global_analyzer("));
    identifiers(&spans.join("\n"))
}

/// True only when one top-level function binds `alias` from a factory that
/// explicitly returns `type_name` and also registers `alias` in that function.
fn factory_alias_registered(main_rs: &str, type_name: &str, alias: &str) -> bool {
    if alias == type_name {
        return false;
    }
    let factories = typed_factory_names(main_rs, type_name);
    if factories.is_empty() {
        return false;
    }
    top_level_functions(main_rs)
        .into_iter()
        .any(|(_, function)| {
            factory_binding_aliases_in(&function, &factories).contains(alias)
                && registration_idents(&function).contains(alias)
        })
}

/// Body of a top-level function, from its signature to the first `}` in
/// column zero. Rustfmt puts the closing brace of a top-level item there, so
/// this avoids brace counting inside format strings.
fn top_level_function(text: &str, name: &str) -> Option<String> {
    let needle = format!("fn {name}(");
    let mut started = false;
    let mut body = Vec::new();
    for line in text.lines() {
        if !started {
            started = ["fn ", "async fn ", "pub fn ", "pub async fn "]
                .iter()
                .any(|prefix| line.starts_with(prefix));
            if !started || !line.contains(&needle) {
                started = false;
                continue;
            }
        }
        body.push(line);
        if body.len() > 1 && line == "}" {
            return Some(body.join("\n"));
        }
    }
    None
}

fn runner_span_idents(main_rs: &str) -> BTreeSet<String> {
    identifiers(&registration_spans(main_rs, "add_runner(").join("\n"))
}

fn analyzer_span_idents(main_rs: &str) -> BTreeSet<String> {
    let mut spans = registration_spans(main_rs, "add_analyzer(");
    spans.extend(registration_spans(main_rs, "add_global_analyzer("));
    identifiers(&spans.join("\n"))
}

#[test]
fn every_production_runner_is_registered() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let main_rs = fs::read_to_string(src.join("main.rs")).expect("read main.rs");
    let runners = production_impls(&src, "Runner");

    for critical in [
        "SslRunner",
        "ProcessRunner",
        "StdioRunner",
        "SystemRunner",
        "OpenClawRunner",
    ] {
        assert!(
            runners.contains(critical),
            "runner scan missed {critical}; found {runners:?}"
        );
    }

    let span_idents = runner_span_idents(&main_rs);
    assert!(
        !span_idents.is_empty(),
        "no add_runner( call found in main.rs"
    );

    for runner in &runners {
        if runner == "AgentRunner" {
            assert!(
                main_rs.contains("AgentRunner::"),
                "AgentRunner is the composition root but is never constructed in main.rs"
            );
            continue;
        }
        assert!(
            wired(runner, &span_idents, &main_rs),
            "{runner} implements Runner but no add_runner(...) in main.rs registers it"
        );
    }
}

#[test]
fn every_production_analyzer_is_registered() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let main_rs = fs::read_to_string(src.join("main.rs")).expect("read main.rs");
    let analyzers = production_impls(&src, "Analyzer");

    for critical in [
        "RawSecretRedactor",
        "StdioSecretRedactor",
        "ResponsibilityRuntime",
        "SecuritySignoffAnalyzer",
    ] {
        assert!(
            analyzers.contains(critical),
            "analyzer scan missed {critical}; found {analyzers:?}"
        );
    }

    let span_idents = analyzer_span_idents(&main_rs);
    assert!(
        !span_idents.is_empty(),
        "no analyzer registration call found in main.rs"
    );

    for analyzer in &analyzers {
        assert!(
            wired(analyzer, &span_idents, &main_rs),
            "{analyzer} implements Analyzer but is never registered in main.rs"
        );
    }
}

/// The whole-run composition (`run_trace`) is the path every benchmark run
/// uses. A type counts as wired only if `run_trace` itself registers it, so
/// deleting one of the two `RawSecretRedactor` sites (the raw-SSL shortcut and
/// the trace chain) is caught rather than masked by the other site.
#[test]
fn trace_composition_registers_every_production_producer() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let main_rs = fs::read_to_string(src.join("main.rs")).expect("read main.rs");
    let trace = top_level_function(&main_rs, "run_trace").expect("run_trace must exist in main.rs");

    let analyzer_idents = analyzer_span_idents(&trace);
    assert!(
        !analyzer_idents.is_empty(),
        "run_trace registers no analyzer; the composition moved and this gate is blind"
    );
    for analyzer in production_impls(&src, "Analyzer") {
        assert!(
            wired(&analyzer, &analyzer_idents, &main_rs),
            "{analyzer} is not registered inside run_trace, the composition every run uses"
        );
    }

    let runner_idents = runner_span_idents(&trace);
    assert!(
        !runner_idents.is_empty(),
        "run_trace registers no runner; the composition moved and this gate is blind"
    );
    for runner in production_impls(&src, "Runner") {
        if runner == "AgentRunner" {
            continue;
        }
        assert!(
            wired(&runner, &runner_idents, &main_rs),
            "{runner} is not added inside run_trace, the composition every run uses"
        );
    }
}

/// `docs/capabilities.json` asserts which analyzers are registered. Until this
/// test existed, nothing compared those claims with the code, so the manifest
/// could assert a control that had been deleted.
#[test]
fn capabilities_analyzer_claims_match_the_code() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let main_rs = fs::read_to_string(src.join("main.rs")).expect("read main.rs");
    let manifest_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../docs/capabilities.json");
    let manifest: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(&manifest_path).expect("read docs/capabilities.json"),
    )
    .expect("parse docs/capabilities.json");

    let analyzers = production_impls(&src, "Analyzer");
    let span_idents = analyzer_span_idents(&main_rs);
    let block = &manifest["analyzers"];
    let names = |key: &str| -> Vec<String> {
        block[key]
            .as_array()
            .unwrap_or_else(|| panic!("capabilities.json analyzers.{key} must be a list"))
            .iter()
            .filter_map(|value| value.as_str().map(str::to_string))
            .collect()
    };
    let registered = names("registered");
    let unregistered = names("implemented_but_not_registered");

    assert!(
        !registered.is_empty(),
        "capabilities.json must list at least one registered analyzer"
    );
    for name in &registered {
        assert!(
            analyzers.contains(name),
            "capabilities.json claims {name} is registered but no `impl Analyzer for {name}` exists"
        );
        assert!(
            wired(name, &span_idents, &main_rs),
            "capabilities.json claims {name} is registered but main.rs never registers it"
        );
    }
    for name in &unregistered {
        assert!(
            analyzers.contains(name),
            "capabilities.json lists {name} as implemented but no `impl Analyzer for {name}` exists"
        );
        assert!(
            !wired(name, &span_idents, &main_rs),
            "capabilities.json lists {name} as implemented_but_not_registered yet main.rs registers it"
        );
    }
}

/// Bounded scanner tests use synthetic `main.rs` snippets so the positive path
/// and each deletion are asserted directly, without a Rust parser or a model
/// of every language construct.
#[cfg(test)]
mod scanner_tests {
    use super::*;

    const FACTORY_POSITIVE: &str = r#"
fn build_runtime() -> Result<Option<Widget>, Error> {
    Ok(Some(Widget::new()))
}

fn run_trace() {
    let mut runtime = build_runtime()?;
    if let Some(mut runtime) = runtime.take() {
        agent.add_global_analyzer(Box::new(runtime));
    }
}
"#;

    fn analyzer_wired(source: &str, type_name: &str) -> bool {
        let span_idents = analyzer_span_idents(source);
        wired(type_name, &span_idents, source)
    }

    #[test]
    fn factory_return_type_plus_call_plus_registration_is_wired() {
        assert!(
            analyzer_wired(FACTORY_POSITIVE, "Widget"),
            "a typed factory binding registered in the same function must count"
        );
    }

    #[test]
    fn factory_wiring_requires_the_binding_call() {
        let without_call =
            FACTORY_POSITIVE.replace("    let mut runtime = build_runtime()?;\n", "");
        assert!(
            !analyzer_wired(&without_call, "Widget"),
            "the return-type declaration and later registration alone must not count"
        );
    }

    #[test]
    fn factory_wiring_requires_the_registration() {
        let without_registration = FACTORY_POSITIVE.replace(
            "        agent.add_global_analyzer(Box::new(runtime));\n",
            "        agent.add_global_analyzer(Box::new(UnrelatedAnalyzer::new()));\n",
        );
        assert!(
            !analyzer_wired(&without_registration, "Widget"),
            "a factory call without a registration of its binding must not count"
        );
    }

    #[test]
    fn uncalled_same_type_factory_does_not_count() {
        let source = r#"
fn build_widget() -> Widget {
    Widget::new()
}

fn run_trace() {
    let unrelated = 1;
}
"#;
        assert!(
            !analyzer_wired(source, "Widget"),
            "an uncalled factory declaration is not a registration"
        );
    }

    #[test]
    fn uncalled_function_binding_is_not_a_global_alias() {
        let source = r#"
fn build_widget() -> Result<Widget, Error> {
    Ok(Widget::new())
}

fn unused() {
    let runtime = build_widget()?;
}

fn run_trace() {
    agent.add_global_analyzer(Box::new(runtime));
}
"#;
        assert!(
            !analyzer_wired(source, "Widget"),
            "a same-named binding in an uncalled function must not alias run_trace"
        );
    }

    #[test]
    fn type_annotation_alone_is_not_registration() {
        let source = r#"
fn run_trace() {
    let runtime: Option<Widget> = None;
    agent.add_global_analyzer(Box::new(runtime));
}
"#;
        assert!(
            !analyzer_wired(source, "Widget"),
            "a type annotation is not a factory call or constructor registration"
        );
    }
}
