// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Content-free GenAI spans for external observability.
//!
//! Scope is `io.mtagentlens`; span names are `mtl.gen_ai.<kind>`. The mapping
//! is deliberately to *our* attributes rather than guessing at a vendor's
//! schema: the stable contract is identifiers, enums, counts and timestamps.
//!
//! **Content never leaves by default.** Prompts, tool arguments, HTTP bodies,
//! alert titles/summaries/reasoning are all attacker-influenced text that may
//! contain credentials. A caller that truly needs them must opt in with
//! [`ExportPolicy::include_content`]; even then the text is passed through
//! [`redact`] and credentials are replaced.
//!
//! Raw transport bytes (`data_b64`) are never part of a span. They stay in the
//! local artifacts for replay; exporting them would defeat the redaction layer.

use std::collections::BTreeMap;
use std::path::Path;

use regex_lite::Regex;
use serde::Serialize;
use serde_json::{Value, json};

use crate::responsibility::types::alert_types::ResponsibilityAlert;

/// OpenTelemetry scope name for every span this module emits.
pub const SCOPE: &str = "io.mtagentlens";

/// Span name prefix. Kept as a constant so tests and consumers cannot drift.
pub const SPAN_PREFIX: &str = "mtl.gen_ai";

/// What a caller is allowed to export.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ExportPolicy {
    /// Export free-form text (titles, summaries, reasoning), redacted.
    /// Off by default: the safe choice must be the default.
    pub include_content: bool,
}

/// A single exported span. Serializes to one JSON object per line.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GenAiSpan {
    pub scope: &'static str,
    pub name: String,
    pub timestamp_ns: u64,
    pub attributes: BTreeMap<String, Value>,
    /// Only present when the caller opted in; redacted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<BTreeMap<String, String>>,
}

/// Build a content-free span for a responsibility alert.
pub fn alert_span(alert: &ResponsibilityAlert, policy: ExportPolicy) -> GenAiSpan {
    let mut attributes = BTreeMap::new();
    attributes.insert("mtl.gen_ai.alert.id".into(), json!(alert.alert_id.as_str()));
    attributes.insert(
        "mtl.gen_ai.alert.decision".into(),
        json!(format!("{:?}", alert.decision)),
    );
    attributes.insert(
        "mtl.gen_ai.alert.severity".into(),
        json!(format!("{:?}", alert.severity)),
    );
    attributes.insert(
        "mtl.gen_ai.alert.authority".into(),
        json!(format!("{:?}", alert.authority)),
    );
    attributes.insert(
        "mtl.gen_ai.alert.display_profile".into(),
        json!(format!("{:?}", alert.display_profile)),
    );
    attributes.insert(
        "mtl.gen_ai.alert.confidence".into(),
        json!(alert.confidence),
    );
    attributes.insert(
        "mtl.gen_ai.alert.primary_agent.id".into(),
        json!(alert.primary_agent.agent_id.as_str()),
    );
    attributes.insert(
        "mtl.gen_ai.alert.involved_agents.count".into(),
        json!(alert.involved_agents.len()),
    );
    attributes.insert(
        "mtl.gen_ai.graph.nodes".into(),
        json!(alert.graph_snapshot.nodes.len()),
    );
    attributes.insert(
        "mtl.gen_ai.graph.edges".into(),
        json!(alert.graph_snapshot.edges.len()),
    );
    attributes.insert(
        "mtl.gen_ai.timeline.steps".into(),
        json!(alert.timeline.steps.len()),
    );
    attributes.insert(
        "mtl.gen_ai.alert.phase".into(),
        json!(match alert.phase {
            crate::responsibility::types::phase::PhaseIndicator::Provisional { .. } =>
                "Provisional",
            crate::responsibility::types::phase::PhaseIndicator::Authoritative { .. } =>
                "Authoritative",
        }),
    );

    let content = policy.include_content.then(|| {
        let mut content = BTreeMap::new();
        content.insert("title".into(), redact(&alert.title));
        content.insert("summary".into(), redact(&alert.summary));
        content.insert("reasoning".into(), redact(&alert.reasoning));
        content
    });

    GenAiSpan {
        scope: SCOPE,
        name: format!("{SPAN_PREFIX}.alert"),
        timestamp_ns: alert.timestamp_ns,
        attributes,
        content,
    }
}

/// Serialize spans as newline-delimited JSON.
pub fn write_jsonl<'a, I>(path: &Path, spans: I) -> std::io::Result<usize>
where
    I: IntoIterator<Item = &'a GenAiSpan>,
{
    use std::io::Write;
    let mut file = std::io::BufWriter::new(std::fs::File::create(path)?);
    let mut written = 0;
    for span in spans {
        let line = serde_json::to_string(span).map_err(std::io::Error::other)?;
        writeln!(file, "{line}")?;
        written += 1;
    }
    file.flush()?;
    Ok(written)
}

// ═══════════════════════════════════════════════════════════════════════════
// Redaction
// ═══════════════════════════════════════════════════════════════════════════

/// Key/label words that make a value sensitive. Shared by the JSON and
/// plain-text rules so the same label cannot leak depending on quoting.
const SENSITIVE_WORDS: &str = "authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|api[_-]?key|access[_-]?key|aws_secret_access_key|secret[_-]?access[_-]?key|secret|token|password|passwd|passphrase|pwd|credential|private[_-]?key";

/// Replace credential-shaped text with `[REDACTED]`.
///
/// Two pattern sets, selected by whether the whole input is valid JSON:
///
/// * **JSON mode** — values are replaced without consuming the enclosing
///   string's quotes, so the payload stays parseable. Covers object members,
///   JSON embedded as a string (escaped quotes), and header/label text that
///   appears *inside* a string value.
/// * **Text mode** — HTTP headers and `key=value` text, where a header value
///   runs to the end of the line.
///
/// A single line-oriented rule set cannot do both: `[^\r\n]+` is correct for
/// an HTTP head and destructive inside a JSON string, and a value class that
/// stops at a quote turns `"password: \"hunter2\""` into a corrupted payload
/// with the secret still present.
pub fn redact(text: &str) -> String {
    let mut out = text.to_string();
    for (pattern, replacement) in patterns(looks_like_json(text)) {
        out = pattern.replace_all(&out, *replacement).to_string();
    }
    out
}

/// Whether the whole input is a JSON object/array.
///
/// `serde_json` is the oracle: a leading brace alone is not enough (a log line
/// may start with one), and parsing also rejects a truncated payload, which
/// then takes the text rules.
fn looks_like_json(text: &str) -> bool {
    let trimmed = text.trim_start();
    (trimmed.starts_with('{') || trimmed.starts_with('['))
        && serde_json::from_str::<serde_json::Value>(trimmed).is_ok()
}

fn patterns(json_like: bool) -> &'static Vec<(Regex, &'static str)> {
    static TEXT: std::sync::OnceLock<Vec<(Regex, &'static str)>> = std::sync::OnceLock::new();
    static JSON: std::sync::OnceLock<Vec<(Regex, &'static str)>> = std::sync::OnceLock::new();
    if json_like {
        JSON.get_or_init(|| build_patterns(true))
    } else {
        TEXT.get_or_init(|| build_patterns(false))
    }
}

/// Each pattern carries its own replacement. Inferring the replacement from
/// the pattern's source text was fragile: adding a JSON form of the same rule
/// silently fell through to `[REDACTED]`, which would have replaced the key
/// and the quotes instead of only the value.
fn build_patterns(json_like: bool) -> Vec<(Regex, &'static str)> {
    let mut specs: Vec<(String, &'static str)> = Vec::new();

    // PEM private key blocks (multi-line; `(?s)` makes `.` span lines).
    specs.push((
        r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----".to_string(),
        "[REDACTED PRIVATE KEY]",
    ));

    if json_like {
        // JSON object members: `"password": "hunter2"`. The key match is any
        // quoted key *containing* a sensitive word (`"AWS_SECRET_ACCESS_KEY"`,
        // `"x-api-key"`); a key like `"token_count"` may be over-redacted,
        // which is the safe direction. Only the value is replaced.
        specs.push((
            format!(r#"(?i)("(?:[^"]*(?:{SENSITIVE_WORDS})[^"]*)"\s*:\s*)"(?:[^"\\]|\\.)*""#),
            "$1\"[REDACTED]\"",
        ));
        // The same key with a non-string value: an array of tokens or a
        // numeric secret. The quoted-value rule above cannot match these, and
        // leaving them because of their JSON type would be arbitrary.
        specs.push((
            format!(
                r#"(?i)("(?:[^"]*(?:{SENSITIVE_WORDS})[^"]*)"\s*:\s*)(\[[^\]]*\]|-?[0-9][0-9.eE+-]*)"#
            ),
            "$1\"[REDACTED]\"",
        ));
        // JSON embedded as a string: the inner quotes are escaped
        // (`{"result":"{\"api_key\": \"opaque\"}"}`). The unescaped-member
        // rule cannot see these, and the plain-text rule cannot either.
        specs.push((
            format!(
                r#"(?i)(\\"(?:[^"\\]*(?:{SENSITIVE_WORDS})[^"\\]*)\\"\s*:\s*)\\"(?:[^"\\]|\\.)*\\""#
            ),
            "$1\\\"[REDACTED]\\\"",
        ));
        // Header-shaped text INSIDE a JSON string value. The value must stop
        // at the enclosing quote/backslash, never at end-of-line: a JSON-RPC
        // message is one line, so `[^\r\n]+` would delete every following
        // member. Structured members were already handled above.
        specs.push((
            r#"(?i)(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|www-authenticate)\s*:\s*[^"\\\r\n]+"#.to_string(),
            "$1: [REDACTED]",
        ));
        specs.push((
            "(?i)bearer\\s+[A-Za-z0-9._~+/=-]+".to_string(),
            "[REDACTED]",
        ));
        // Label/value inside a JSON string: consume escaped quotes, stop at
        // the enclosing (unescaped) quote.
        specs.push((
            format!(r#"(?i)({SENSITIVE_WORDS})\s*[=:]\s*(?:\\"|[^"\\\s,;])+"#),
            "$1=[REDACTED]",
        ));
    } else {
        // Authorization / cookie headers, including folded lines.
        specs.push((
            r"(?i)(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|www-authenticate)\s*:\s*[^\r\n]+".to_string(),
            "$1: [REDACTED]",
        ));
        specs.push((
            "(?i)bearer\\s+[A-Za-z0-9._~+/=-]+".to_string(),
            "[REDACTED]",
        ));
        // key=value / key: value secrets. Quoted values (single or double)
        // are consumed whole, so `password: "hunter2"` is redacted rather
        // than skipped.
        specs.push((
            format!(r#"(?i)({SENSITIVE_WORDS})\s*[=:]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)"#),
            "$1=[REDACTED]",
        ));
    }

    // Vendor prefixes are shape-based and safe in both modes.
    specs.push((r"sk-ant-[A-Za-z0-9_-]{8,}".to_string(), "[REDACTED]"));
    specs.push((r"sk-[A-Za-z0-9]{20,}".to_string(), "[REDACTED]"));
    specs.push((r"AKIA[0-9A-Z]{16}".to_string(), "[REDACTED]"));
    specs.push((r"gh[pousr]_[A-Za-z0-9]{20,}".to_string(), "[REDACTED]"));

    specs
        .into_iter()
        .map(|(source, replacement)| {
            (
                Regex::new(&source).expect("redaction pattern must compile"),
                replacement,
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::alert_types::{
        AgentRef, AlertAuthority, AlertDecision, AlertSeverity, BehaviorTimeline, DisplayProfile,
        EngineSection,
    };
    use crate::responsibility::types::graph_types::{GraphMetrics, GraphSnapshot};
    use crate::responsibility::types::ids::{AgentId, AlertId};
    use crate::responsibility::types::phase::PhaseIndicator;

    fn alert(title: &str, summary: &str, reasoning: &str) -> ResponsibilityAlert {
        ResponsibilityAlert {
            alert_id: AlertId::from("alert-1"),
            timestamp_ns: 1_700_000_000_000_000_000,
            display_profile: DisplayProfile::UnsafeByEngine,
            decision: AlertDecision::Unsafe,
            severity: AlertSeverity::Warning,
            confidence: 0.87,
            authority: AlertAuthority::Supporting,
            reasoning: reasoning.into(),
            title: title.into(),
            summary: summary.into(),
            action_hint: None,
            primary_agent: AgentRef {
                agent_id: AgentId::from("agent-1"),
                claimed_role: None,
            },
            involved_agents: vec![AgentRef {
                agent_id: AgentId::from("agent-2"),
                claimed_role: None,
            }],
            engine: EngineSection::default(),
            timeline: BehaviorTimeline {
                steps: vec![],
                total_duration_ms: 0,
            },
            graph_snapshot: GraphSnapshot {
                snapshot_id: "snap-1".into(),
                timestamp_ns: 1,
                nodes: vec![],
                edges: vec![],
                metrics: GraphMetrics::default(),
            },
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
            provisional_ref: None,
        }
    }

    const SECRET: &str = "sk-ant-api03-REAL-LEAK-abcdefghijklmnopqrstuvwxyz";

    #[test]
    fn default_export_contains_no_content() {
        let alert = alert(
            &format!("exfiltrated {SECRET}"),
            "Authorization: Bearer abc123456789",
            "the agent read /etc/shadow then posted it",
        );
        let span = alert_span(&alert, ExportPolicy::default());
        let json = serde_json::to_string(&span).unwrap();

        assert!(span.content.is_none());
        assert!(!json.contains(SECRET), "credential leaked: {json}");
        assert!(!json.contains("Bearer"), "header text leaked: {json}");
        assert!(!json.contains("/etc/shadow"), "content leaked: {json}");
        assert!(!json.contains("exfiltrated"));
        assert_eq!(span.scope, SCOPE);
        assert_eq!(span.name, "mtl.gen_ai.alert");
        assert_eq!(
            span.attributes["mtl.gen_ai.alert.decision"],
            json!("Unsafe")
        );
        assert_eq!(
            span.attributes["mtl.gen_ai.alert.involved_agents.count"],
            json!(1)
        );
    }

    #[test]
    fn explicit_content_export_is_redacted() {
        let alert = alert(
            &format!("key {SECRET}"),
            "Authorization: Bearer abc123456789",
            "password=hunter2",
        );
        let span = alert_span(
            &alert,
            ExportPolicy {
                include_content: true,
            },
        );
        let json = serde_json::to_string(&span).unwrap();
        assert!(!json.contains(SECRET), "credential leaked: {json}");
        assert!(!json.contains("abc123456789"));
        assert!(!json.contains("hunter2"));
        assert!(json.contains("[REDACTED]"));
        assert!(span.content.is_some());
    }

    #[test]
    fn redaction_is_shape_based_and_idempotent() {
        let cases = [
            ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
            ("authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
            ("Cookie: session=abc123", "abc123"),
            ("api_key=abcd1234", "abcd1234"),
            ("password: hunter2", "hunter2"),
            ("password: \"hunter2\"", "hunter2"),
            ("api_key = \"abc123\"", "abc123"),
            ("private_key=abc123", "abc123"),
            ("credential=abc123", "abc123"),
            ("pwd=abc123", "abc123"),
            ("passphrase=abc123", "abc123"),
            (
                "token=ghp_abcdefghijklmnopqrstuvwxyz0123",
                "ghp_abcdefghijklmnopqrstuvwxyz0123",
            ),
            ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
            (SECRET, SECRET),
            (
                "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
                "MIIEow",
            ),
        ];
        for (input, secret) in cases {
            let once = redact(input);
            assert!(!once.contains(secret), "{input:?} leaked as {once:?}");
            assert_eq!(redact(&once), once, "redaction must be idempotent");
        }
        // Ordinary text is untouched.
        assert_eq!(
            redact("agent read /etc/hosts at 12:00"),
            "agent read /etc/hosts at 12:00"
        );
    }

    /// MCP stdio traffic is JSON-RPC. A sensitive key there is quoted, so the
    /// plain-text rules (which expect `key=value`/`key: value`) never fire.
    /// These are the exact shapes a tool call emits.
    #[test]
    fn json_rpc_payloads_are_redacted_without_corrupting_the_json() {
        let cases = [
            (
                r#"{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"db","arguments":{"password":"hunter2"}}}"#,
                "hunter2",
            ),
            (
                r#"{"params":{"headers":{"x-api-key":"abc123def456"}}}"#,
                "abc123def456",
            ),
            (
                r#"{"env":{"AWS_SECRET_ACCESS_KEY":"wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}}"#,
                "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            (r#"{"Authorization":"Basic dXNlcjpwYXNz"}"#, "dXNlcjpwYXNz"),
            (
                r#"{"client_secret":"s3cr3t-value","note":"keep me"}"#,
                "s3cr3t-value",
            ),
            // A labelled value inside a JSON string, with the quotes escaped
            // by the outer JSON. The plain rule used to consume the backslash
            // and leave the secret outside its string.
            (r#"{"text":"password: \"hunter2\""}"#, "hunter2"),
            // Header-shaped text inside a JSON string. The line-oriented
            // header rule used to delete the rest of the payload.
            (
                r#"{"text":"Authorization: Bearer abc123\nsafe: keep"}"#,
                "abc123",
            ),
            (r#"{"note":"Cookie: a=1; b=2","safe":"keep"}"#, "a=1"),
            // JSON embedded as a string: inner quotes are escaped.
            (
                r#"{"result":"{\"api_key\": \"opaque-Value-xyz\"}"}"#,
                "opaque-Value-xyz",
            ),
            (r#"{"note":"api_key: \"abc123\"","x":"y"}"#, "abc123"),
            // Non-string values under a sensitive key.
            (r#"{"tokens":["alpha","beta"],"safe":"keep"}"#, "alpha"),
            (r#"{"password":987654321,"safe":"keep"}"#, "987654321"),
        ];
        for (input, secret) in cases {
            let once = redact(input);
            assert!(!once.contains(secret), "{input:?} leaked as {once:?}");
            assert_eq!(redact(&once), once, "redaction must be idempotent");
            // The payload must still parse: redacting a value, not the key.
            let parsed: serde_json::Value =
                serde_json::from_str(&once).unwrap_or_else(|e| panic!("{once:?}: {e}"));
            assert!(parsed.is_object());
            // Non-secret text survives.
            if input.contains("keep me") {
                assert!(once.contains("keep me"));
            }
            if input.contains("safe: keep") {
                assert!(once.contains("safe: keep"), "{once:?}");
            }
            if input.contains("\"safe\":\"keep\"") {
                assert!(once.contains("\"safe\":\"keep\""), "{once:?}");
            }
        }
    }

    #[test]
    fn jsonl_writer_emits_one_object_per_span() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("genai.jsonl");
        let spans = [
            alert_span(&alert("a", "b", "c"), ExportPolicy::default()),
            alert_span(&alert("d", "e", "f"), ExportPolicy::default()),
        ];
        let count = write_jsonl(&path, spans.iter()).unwrap();
        assert_eq!(count, 2);
        let text = std::fs::read_to_string(&path).unwrap();
        assert_eq!(text.lines().count(), 2);
        for line in text.lines() {
            let value: serde_json::Value = serde_json::from_str(line).unwrap();
            assert_eq!(value["scope"], SCOPE);
        }
    }
}
