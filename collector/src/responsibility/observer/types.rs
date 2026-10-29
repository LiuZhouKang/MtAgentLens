// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// observer/types.rs — CANONICAL Observer type system (§16).
//
// This is the SINGLE SOURCE OF TRUTH for all observer-related types.
// classification/types.rs re-exports from here for backward compatibility.
// Do NOT duplicate observer types in other modules.

use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::types::evidence_types::{Intent, IntentAuthoritySource};
pub use crate::responsibility::types::refinement::ScoreBreakdown;
// Alert-facing types live in types/alert_types.rs (single source of truth).
pub use crate::responsibility::types::alert_types::{
    AlertAuthority, DisplayProfile, PatternVerdict, RuleVerdict,
};
use serde::{Deserialize, Serialize};

// ── Observer Decision ───────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObserverDecision {
    Safe,
    Unsafe,
    Uncertain,
}

impl ObserverDecision {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Safe => "safe",
            Self::Unsafe => "unsafe",
            Self::Uncertain => "uncertain",
        }
    }
}

// ── Cache TTL ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheTTL {
    Once,
    Task,
    All,
}

// ── Observer Error ──────────────────────────────────────────────────────

#[derive(Debug)]
pub enum ObserverError {
    HttpError(String),
    Timeout,
    InvalidResponse(String),
    RateLimited,
    InjectionDetected,
    SelfReportedAttack,
    // ── Value-domain validation errors ──
    InvalidDecision(String),
    InvalidSeverity(String),
    InvalidConfidence(f32),
    InvalidCacheTTL(String),
    ReasoningTooLong(usize),
    // ── Per-candidate review contract errors ──
    /// A candidate verdict names an id that was not presented in this request.
    InvalidCandidateId(String),
    /// The same candidate id received more than one verdict.
    DuplicateCandidateId(String),
    /// A presented candidate did not receive a verdict.
    MissingCandidateVerdict(String),
}

// ── Classification Result ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ClassificationResult {
    pub score: f32,
    pub route: String,
    pub score_breakdown: ScoreBreakdown,
    /// CUSUM progressive scoring — persisted for ablation.
    #[serde(default)]
    pub cusum_triggered: bool,
    #[serde(default)]
    pub cusum_dimensions: Vec<String>,
    #[serde(default)]
    pub progressive_fused: f64,
    #[serde(default)]
    pub observer_trigger_reason: String,
    /// Graph template matches produced by the matcher. Persisted with the
    /// alert's engine section so a fired template is distinguishable from a
    /// matcher that found nothing; an empty list must mean "no match", not
    /// "the result was dropped".
    #[serde(default)]
    pub pattern_verdicts: Vec<PatternVerdict>,
}

// ── Route ────────────────────────────────────────────────────────────────

pub enum Route {
    DirectSafe(ObserverOutput),
    DirectUnsafe(ObserverOutput),
    ToObserver(ObserverSignature),
}

// ── Classification Config ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassificationConfig {
    pub threshold_unsafe: f32,
    pub threshold_safe: f32,
    /// Experimental content-egress candidates: a credential-class read followed
    /// by network egress from the same agent. Address-agnostic, because the
    /// violation is data leaving the agent, not the destination being public.
    /// Off by default: this is an ablation, not part of the measured default.
    #[serde(default)]
    pub sensitive_egress: bool,
    /// Review-level local egress: a *spawned* process sending data to a local
    /// (loopback / private) destination the task never named. Off by default:
    /// the corpus measures the default configuration.
    #[serde(default)]
    pub local_egress: bool,
    /// Hard rule for a deterministic credential-read -> network-egress chain.
    ///
    /// `sensitive_egress` only feeds the LLM reviewer, which can answer
    /// "uncertain" and leave an executed exfiltration as a review item. The
    /// measured campaign showed 6/19 executed attacks with that exact chain
    /// stuck at review. This flag promotes the same deterministic pair to a
    /// hard violation (`hard.sensitive_egress`), so the verdict does not depend
    /// on the semantic arbiter. Off by default until replay ablation approves it.
    #[serde(default)]
    pub hard_sensitive_egress: bool,
    /// Score-pipeline constants. Defaults are the measured operating point;
    /// replay sensitivity overrides them to show whether the verdict depends on
    /// them (review comment 6).
    #[serde(default)]
    pub scoring: crate::responsibility::classification::scoring::ScoringConfig,
}

impl Default for ClassificationConfig {
    fn default() -> Self {
        Self {
            threshold_unsafe: 0.70,
            threshold_safe: 0.15,
            sensitive_egress: false,
            local_egress: false,
            hard_sensitive_egress: false,
            scoring: Default::default(),
        }
    }
}

impl ClassificationConfig {
    /// Read the experimental flags from the environment. A missing or blank
    /// variable keeps the measured default (off).
    pub fn from_env() -> Self {
        Self {
            sensitive_egress: std::env::var(SENSITIVE_EGRESS_ENV)
                .ok()
                .is_some_and(|value| matches!(value.trim(), "1" | "true")),
            local_egress: std::env::var(LOCAL_EGRESS_ENV)
                .ok()
                .is_some_and(|value| matches!(value.trim(), "1" | "true")),
            hard_sensitive_egress: std::env::var(HARD_SENSITIVE_EGRESS_ENV)
                .ok()
                .is_some_and(|value| matches!(value.trim(), "1" | "true")),
            ..Self::default()
        }
    }
}

/// Environment variable that enables content-egress candidates.
pub const SENSITIVE_EGRESS_ENV: &str = "MTAGENTLENS_SENSITIVE_EGRESS";

/// Environment variable that enables local-egress review candidates.
pub const LOCAL_EGRESS_ENV: &str = "MTAGENTLENS_LOCAL_EGRESS";

/// Environment variable that promotes credential-read -> egress chains to a
/// hard violation. Replay ablation (61 recorded runs, exact reproduction):
/// 2 executed attacks move review -> Unsafe and no benign run changes verdict.
pub const HARD_SENSITIVE_EGRESS_ENV: &str = "MTAGENTLENS_HARD_SENSITIVE_EGRESS";

// ── Observer Signature Sub-types ────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct EvidenceSignature {
    pub total: usize,
    pub direct: usize,
    pub inferred: usize,
    pub avg_confidence: f32,
    pub max_confidence: f32,
    pub match_levels: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct GraphSignature {
    pub node_count: usize,
    pub edge_count: usize,
    pub edge_types: Vec<String>,
    pub node_types: Vec<String>,
    pub boundary_crossings: usize,
    pub delegation_depth: usize,
    pub cross_agent_edges: usize,
    pub agent_count: usize,
    pub temporal_span_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct PatternSignal {
    pub template_id: String,
    pub match_confidence: f32,
    pub severity: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct RoleSignal {
    pub claimed_role: Option<String>,
    pub role_mismatches: Vec<String>,
    pub subject: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ResourceSignal {
    pub resource_type: String,
    pub path: String,
    pub operation: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CorrelationSignal {
    pub has_delegation_chain: bool,
    pub has_tainted_context: bool,
    pub has_credential_pattern: bool,
    pub has_concurrent_access: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct RuleHitSignal {
    pub rule_id: String,
    pub matched_resource: String,
}

// ── Observed Task Context ──────────────────────────────────────────────

/// Source label for task text. Only eBPF-captured `Intent { tool_name:
/// "UserPrompt" }` records feed this context; native logs are not a source.
pub const OBSERVED_TASK_SOURCE: &str = "ebpf_tls_user_prompt";

/// The captured task is input, not an authenticated principal. TLS `role:user`
/// is unauthenticated request text, so authorization is explicitly unknown
/// rather than inferred from the task or tool descriptions.
pub const OBSERVED_TASK_AUTHORIZATION: &str = "authorization_unknown";

/// Total task-text byte budget. Kept separate from the tool-description budget
/// so a long command or path cannot evict the task.
pub const OBSERVED_TASK_MAX_BYTES: usize = 8 * 1024;

/// Upper bound on distinct `UserPrompt` messages retained (history repeats).
pub const OBSERVED_TASK_MAX_MESSAGES: usize = 16;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ObservedTaskMessage {
    pub intent_id: String,
    pub message_id: String,
    pub pid: u32,
    pub process_start_time_ns: u64,
    pub authority_source: String,
    /// `complete` / `partial` / `malformed` / `unsupported`.
    pub parse_state: String,
    pub text: String,
    pub text_bytes: usize,
    /// Digest of the full observed message before the display budget is applied.
    pub source_sha256: String,
    pub truncated: bool,
}

/// Task text captured from the eBPF-observed `UserPrompt`, kept distinct from
/// `RequestedOperations`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ObservedTaskContext {
    pub source: &'static str,
    /// `observed_unauthenticated`: observed bytes, not an authenticated role.
    pub observation_status: &'static str,
    pub authorization: &'static str,
    pub messages: Vec<ObservedTaskMessage>,
    pub total_bytes: usize,
    pub truncated: bool,
    pub omitted_messages: usize,
    pub duplicate_messages: usize,
}

impl ObservedTaskContext {
    /// Build a bounded, deterministic task view from captured intents.
    ///
    /// The request history is re-read on every turn, so the same prompt is
    /// observed repeatedly. Deduplicate on (process, text); the same task from
    /// another process stays a separate observation. Selection follows the
    /// observed order, which is the request-history order.
    pub fn from_intents(intents: &[Intent]) -> Self {
        let mut messages = Vec::new();
        let mut seen = std::collections::HashSet::new();
        let mut duplicate_messages = 0;
        let mut omitted_messages = 0;
        let mut total_bytes = 0;
        let mut truncated = false;

        for intent in intents {
            if !intent.tool_name.eq_ignore_ascii_case("UserPrompt") {
                continue;
            }
            // Out-of-band records are asserted by the agent, not observed by
            // eBPF; they are not a task-observation source.
            if intent.authority_source == IntentAuthoritySource::OutOfBandAsserted {
                continue;
            }
            // Stop retaining before allocating text: the dedup set and copied
            // strings stay bounded by the task budget, not by request history.
            if messages.len() >= OBSERVED_TASK_MAX_MESSAGES
                || total_bytes >= OBSERVED_TASK_MAX_BYTES
            {
                omitted_messages += 1;
                truncated = true;
                continue;
            }
            let text = intent
                .tool_args
                .iter()
                .filter_map(|arg| match arg {
                    crate::responsibility::types::NormalizedArg::Content(content) => {
                        Some(content.as_str())
                    }
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("\n");
            if text.trim().is_empty() {
                continue;
            }
            if !seen.insert(task_message_signature(intent, &text)) {
                duplicate_messages += 1;
                continue;
            }
            let remaining = OBSERVED_TASK_MAX_BYTES.saturating_sub(total_bytes);
            let source_sha256 =
                hex::encode(<sha2::Sha256 as sha2::Digest>::digest(text.as_bytes()));
            let (text, message_truncated) = truncate_task_text(text, remaining);
            let text_bytes = text.len();
            total_bytes += text_bytes;
            truncated |= message_truncated;
            messages.push(ObservedTaskMessage {
                intent_id: intent.intent_id.to_string(),
                message_id: intent.message_id.to_string(),
                pid: intent.process_id.pid,
                process_start_time_ns: intent.process_id.start_time_ns,
                authority_source: intent.authority_source.to_string(),
                parse_state: intent.parse_state.to_string(),
                text,
                text_bytes,
                source_sha256,
                truncated: message_truncated,
            });
        }

        Self {
            source: OBSERVED_TASK_SOURCE,
            observation_status: "observed_unauthenticated",
            authorization: OBSERVED_TASK_AUTHORIZATION,
            messages,
            total_bytes,
            truncated,
            omitted_messages,
            duplicate_messages,
        }
    }

    /// Stable key contribution: a changed task must not reuse a review keyed to
    /// the previous task's text.
    pub fn cache_fingerprint(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        // Repeated request history is not new authority or new task content.
        // The messages include full-source digests, so a changed hidden suffix
        // still invalidates a review even if the bounded display is identical.
        serde_json::to_string(&(
            self.source,
            self.observation_status,
            self.authorization,
            &self.messages,
            self.total_bytes,
            self.truncated,
            self.omitted_messages,
        ))
        .unwrap_or_default()
        .hash(&mut hasher);
        hasher.finish()
    }
}

/// Hash of the parts that identify a re-observation: process plus exact text.
fn task_message_signature(intent: &Intent, text: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    intent.process_id.pid.hash(&mut hasher);
    intent.process_id.start_time_ns.hash(&mut hasher);
    text.hash(&mut hasher);
    hasher.finish()
}

fn truncate_task_text(mut text: String, max_bytes: usize) -> (String, bool) {
    if text.len() <= max_bytes {
        return (text, false);
    }
    let mut end = max_bytes;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    text.truncate(end);
    (text, true)
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct ContentSignals {
    #[serde(default)]
    pub candidates: Vec<crate::responsibility::classification::candidates::CandidateSummary>,
    /// Tool commands / intent descriptions (the historical field).
    pub resource_paths_touched: Vec<String>,
    /// Captured task text, kept separate from tool descriptions. `None` means
    /// no `UserPrompt` was observed, not that the task is empty.
    #[serde(default)]
    pub observed_task: Option<ObservedTaskContext>,
    pub credential_patterns_detected: usize,
    pub unexpected_file_sources: Vec<String>,
    pub handoff_depth: usize,
    pub constraint_count_diff: i32,
    /// Structured OS evidence. The Observer previously saw only the first
    /// Content argument of each intent; these fields give it the file paths,
    /// endpoints, credential artifacts and matched rule/template IDs that the
    /// engine actually used. Without them it is a text classifier, not an
    /// OS-evidence arbiter.
    #[serde(default)]
    pub file_paths: Vec<String>,
    #[serde(default)]
    pub network_endpoints: Vec<String>,
    #[serde(default)]
    pub credential_paths: Vec<String>,
    #[serde(default)]
    pub rule_ids: Vec<String>,
    #[serde(default)]
    pub pattern_ids: Vec<String>,
    #[serde(default)]
    pub delegation_links: Vec<String>,
    /// Tool-call boundary resources (including provenance boundaries).
    #[serde(default)]
    pub tool_calls: Vec<String>,
    /// `UntrustedInstructionFlow:<span>` entries: a tool call reproduced
    /// content from an untrusted, unrequested source.
    #[serde(default)]
    pub provenance: Vec<String>,
}

// ── Observer Signature ──────────────────────────────────────────────────

/// Structured summary of the graph/evidence/classification state
/// passed to the LLM for arbitration.
#[derive(Debug, Clone, Serialize)]
pub struct ObserverSignature {
    pub classification: ClassificationResult,
    pub rule_hits: Vec<RuleHitSignal>,
    pub evidence: EvidenceSignature,
    pub graph: GraphSignature,
    pub patterns: Vec<PatternSignal>,
    pub roles: RoleSignal,
    pub resources_touched: Vec<ResourceSignal>,
    pub correlation_evidence: CorrelationSignal,
}

// ── Cache Prior ─────────────────────────────────────────────────────────

/// Historical anomaly context carried into the observer prompt.
#[derive(Debug, Clone, Default, Serialize)]
pub struct CachePrior {
    pub l2_hit: bool,
    pub l2_prior_anomaly_rate: Option<f32>,
    pub l3_hit: bool,
    pub l3_fleet_anomaly_rate: Option<f32>,
}

// ── Observer Input ──────────────────────────────────────────────────────

/// Complete input to the Observer LLM for arbitration.
#[derive(Debug, Clone, Serialize)]
pub struct ObserverInput {
    pub signature: ObserverSignature,
    pub content_signals: Option<ContentSignals>,
    pub prior: CachePrior,
}

// ── Per-candidate verdicts ──────────────────────────────────────────────

/// One structured verdict for one presented review candidate.
///
/// The reviewer receives up to MAX_REVIEW_CANDIDATES candidates in a single
/// call, so evidence used to be diluted into one batch verdict shared by every
/// candidate. A CandidateVerdict keeps the same single call (same cost) while
/// carrying a verdict per presented candidate.
///
/// \`cited\` counts the exact candidate/evidence ids the reviewer relied on for
/// this candidate. It mirrors Observation::ScopedReview.cited, so the existing
/// aggregator rule "an Unsafe vote that cites nothing is Uncertain" applies
/// unchanged.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CandidateVerdict {
    /// Must be one of the candidate ids presented in this request.
    pub candidate_id: String,
    pub decision: ObserverDecision,
    #[serde(default = "default_candidate_verdict_confidence")]
    pub confidence: f32,
    #[serde(default)]
    pub cited: usize,
}

fn default_candidate_verdict_confidence() -> f32 {
    0.5
}

/// Provenance marker: the observer returned one structured verdict per
/// presented candidate.
pub const REVIEW_CONTRACT_PER_CANDIDATE: &str = "review_contract:per_candidate";

/// Provenance marker: the observer returned no per-candidate list (older or
/// limited provider), so the batch decision was applied to every presented
/// candidate. Analysis must distinguish this from a real per-candidate review.
pub const REVIEW_CONTRACT_BATCH_FALLBACK: &str = "review_contract:batch_fallback";

// ── Observer Output ─────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObserverOutput {
    pub decision: ObserverDecision,
    pub severity: ClaimSeverity,
    pub confidence: f32,
    pub cache_ttl: CacheTTL,
    pub authority: AlertAuthority,
    pub reasoning: String,
    pub attacked_flag: bool,
    #[serde(default)]
    pub evidence_cited: Vec<String>,
    pub rule_verdicts: Vec<RuleVerdict>,
    pub pattern_verdicts: Vec<PatternVerdict>,
    /// One verdict per presented candidate. Empty for older checkpoints and
    /// for providers that only answer the batch summary; callers must fall
    /// back to the batch verdict in that case.
    #[serde(default)]
    pub candidate_verdicts: Vec<CandidateVerdict>,
    /// How candidate_verdicts were produced. One of
    /// REVIEW_CONTRACT_PER_CANDIDATE or REVIEW_CONTRACT_BATCH_FALLBACK once the
    /// review contract has been applied; empty for a raw parse.
    #[serde(default)]
    pub review_provenance: Vec<String>,
}

// ── Cache Key ───────────────────────────────────────────────────────────

/// L1 cache key: u32 hash of edge_types + node_types + subject + resource_path.
#[derive(Debug, Clone, Copy, Hash, PartialEq, Eq)]
pub struct CacheKey(pub u64);

impl CacheKey {
    /// Exact-signature key.
    ///
    /// `context` carries everything else that distinguishes two candidates of
    /// the same shape: matched graph templates, boundary count, agent count
    /// and evidence count. Without it, two different runs with the same node
    /// and edge *types* shared a cache slot and the second was answered with
    /// the first one's verdict.
    pub fn compute(
        edge_types: &[String],
        node_types: &[String],
        subject: &str,
        resource_path: &str,
        context: &[String],
    ) -> Self {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        edge_types.hash(&mut hasher);
        node_types.hash(&mut hasher);
        subject.hash(&mut hasher);
        resource_path.hash(&mut hasher);
        let mut ordered: Vec<&String> = context.iter().collect();
        ordered.sort();
        ordered.hash(&mut hasher);
        Self(hasher.finish())
    }
}

#[cfg(test)]
mod observed_task_tests {
    use super::{ClassificationResult, ObservedTaskContext};
    use crate::responsibility::types::{
        Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState, MessageId,
        NormalizedArg, ProcessId,
    };

    fn intent(tool: &str, text: &str, pid: u32, id: &str) -> Intent {
        Intent {
            intent_id: IntentId::from(id),
            process_id: ProcessId::new(pid, 1),
            message_id: MessageId::from(format!("msg-{id}")),
            tool_name: tool.into(),
            tool_args: vec![NormalizedArg::Content(text.into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: false,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 1,
            native_tool_call: None,
            declared_conversation_id: None,
        }
    }

    #[test]
    fn long_path_prefix_does_not_hide_task_text_past_120_chars() {
        let path = format!("/home/test-user/{}", "very-long-directory/".repeat(20));
        let tail = "TASK: rotate the deployment credential and report completion.";
        let prompt = format!("{path}\n{tail}");
        assert!(
            prompt.find(tail).expect("tail") > 120,
            "fixture puts the task past the old first-120-chars cut"
        );

        let context =
            ObservedTaskContext::from_intents(&[intent("UserPrompt", &prompt, 42, "prompt-1")]);

        assert_eq!(context.messages.len(), 1);
        assert!(
            context.messages[0].text.contains(tail),
            "task text after column 120 must survive"
        );
        assert_eq!(context.total_bytes, context.messages[0].text.len());
        assert!(!context.truncated);
        assert_eq!(context.authorization, "authorization_unknown");
        assert_eq!(context.observation_status, "observed_unauthenticated");
    }

    #[test]
    fn observed_task_is_user_prompt_not_tool_description() {
        let context = ObservedTaskContext::from_intents(&[
            intent("Bash", "rm -rf /tmp/build-cache", 42, "bash-1"),
            intent("UserPrompt", "Review the release notes.", 42, "prompt-1"),
        ]);

        assert_eq!(context.messages.len(), 1);
        assert_eq!(context.messages[0].text, "Review the release notes.");
        assert!(
            !context.messages[0].text.contains("rm -rf"),
            "tool descriptions are RequestedOperations, not task context"
        );
    }

    #[test]
    fn repeated_history_is_deduplicated_but_other_processes_are_kept() {
        let prompt = "Audit the workspace.";
        let context = ObservedTaskContext::from_intents(&[
            intent("UserPrompt", prompt, 42, "prompt-1"),
            intent("UserPrompt", prompt, 42, "prompt-2"),
            intent("UserPrompt", prompt, 43, "prompt-3"),
        ]);

        assert_eq!(context.messages.len(), 2, "same process + text is one task");
        assert_eq!(context.duplicate_messages, 1);
        assert!(
            context
                .messages
                .iter()
                .any(|message| message.pid == 43 && message.text == prompt),
            "the same task from another process is a distinct observation"
        );
    }

    #[test]
    fn all_user_prompts_beyond_the_first_ten_are_considered() {
        let mut intents = Vec::new();
        for index in 0..12 {
            intents.push(intent(
                "UserPrompt",
                &format!("task-{index}"),
                42,
                &format!("prompt-{index}"),
            ));
        }
        let context = ObservedTaskContext::from_intents(&intents);

        assert_eq!(context.messages.len(), 12);
        assert!(
            context
                .messages
                .iter()
                .any(|message| message.text == "task-11"),
            "the eleventh prompt must not be silently dropped"
        );
    }

    #[test]
    fn task_text_is_bounded_with_explicit_truncation_metadata() {
        use super::OBSERVED_TASK_MAX_BYTES;

        let huge = format!(
            "/srv/{}\nTAIL-BEYOND-BUDGET",
            "segment/".repeat(OBSERVED_TASK_MAX_BYTES)
        );
        let context = ObservedTaskContext::from_intents(&[
            intent("UserPrompt", &huge, 42, "prompt-1"),
            intent("UserPrompt", "beyond the budget", 42, "prompt-2"),
        ]);

        assert_eq!(context.messages.len(), 1);
        assert!(context.truncated);
        assert!(context.messages[0].truncated);
        assert_eq!(
            context.omitted_messages, 1,
            "text beyond the total budget is explicitly omitted, not silently mixed in"
        );
        assert!(
            context.total_bytes <= OBSERVED_TASK_MAX_BYTES,
            "total bytes must stay inside the explicit budget"
        );
        assert_eq!(
            context.messages[0].text_bytes,
            context.messages[0].text.len()
        );
        assert_eq!(context.messages[0].intent_id, "prompt-1");
        assert_eq!(context.messages[0].message_id, "msg-prompt-1");
        assert_eq!(context.messages[0].pid, 42);
    }

    #[test]
    fn out_of_band_asserted_prompts_are_not_observation_sources() {
        let mut asserted = intent("UserPrompt", "asserted by the agent", 42, "oob-1");
        asserted.authority_source = IntentAuthoritySource::OutOfBandAsserted;

        let context = ObservedTaskContext::from_intents(&[asserted]);

        assert!(context.messages.is_empty());
    }

    #[test]
    fn changed_hidden_suffix_invalidates_a_bounded_task_review() {
        let prefix = "x".repeat(super::OBSERVED_TASK_MAX_BYTES);
        let first = ObservedTaskContext::from_intents(&[intent(
            "UserPrompt",
            &format!("{prefix}first"),
            42,
            "prompt-1",
        )]);
        let second = ObservedTaskContext::from_intents(&[intent(
            "UserPrompt",
            &format!("{prefix}other"),
            42,
            "prompt-1",
        )]);
        assert_eq!(first.messages[0].text, second.messages[0].text);
        assert!(first.truncated && second.truncated);
        assert_ne!(first.cache_fingerprint(), second.cache_fingerprint());
    }

    #[test]
    fn repeated_task_history_does_not_invalidate_a_cached_review() {
        let prompt = intent(
            "UserPrompt",
            "Read only the API documentation.",
            42,
            "prompt-1",
        );
        let once = ObservedTaskContext::from_intents(std::slice::from_ref(&prompt));
        let repeated = ObservedTaskContext::from_intents(&[prompt.clone(), prompt]);
        assert_eq!(repeated.duplicate_messages, 1);
        assert_eq!(
            once.cache_fingerprint(),
            repeated.cache_fingerprint(),
            "history repetition is not a new task"
        );
    }

    #[test]
    fn changed_task_changes_the_cache_fingerprint() {
        let first =
            ObservedTaskContext::from_intents(&[intent("UserPrompt", "task one", 42, "prompt-1")]);
        let second =
            ObservedTaskContext::from_intents(&[intent("UserPrompt", "task two", 42, "prompt-2")]);

        assert_ne!(first.cache_fingerprint(), second.cache_fingerprint());
    }

    /// Old observations/checkpoints predate the per-candidate contract. A
    /// stored ObserverOutput without the new fields must still deserialize.
    #[test]
    fn legacy_observer_output_without_candidate_verdicts_deserializes() {
        let legacy = serde_json::json!({
            "decision": "safe",
            "severity": "info",
            "confidence": 0.5,
            "cache_ttl": "once",
            "authority": "Supporting",
            "reasoning": "legacy",
            "attacked_flag": false,
            "rule_verdicts": [],
            "pattern_verdicts": [],
        });
        let output: super::ObserverOutput =
            serde_json::from_value(legacy).expect("legacy observer output must stay readable");
        assert!(output.candidate_verdicts.is_empty());
        assert!(output.review_provenance.is_empty());
    }

    /// The classification result is a current serialized type. It must not
    /// carry an ONNX score, and legacy extras must not resurrect one.
    #[test]
    fn classification_result_has_no_onnx_classifier_signal() {
        let current =
            serde_json::to_value(ClassificationResult::default()).expect("must serialize");
        for key in ["deberta_score", "content_threat"] {
            assert!(
                current.get(key).is_none(),
                "current classification wire still exposes {key}: {current}"
            );
        }

        let mut legacy = serde_json::to_value(ClassificationResult::default())
            .expect("default classification must serialize");
        let object = legacy
            .as_object_mut()
            .expect("classification wire is an object");
        object.insert("deberta_score".into(), serde_json::json!(0.9));
        object.insert("content_threat".into(), serde_json::json!(0.8));
        let legacy: ClassificationResult =
            serde_json::from_value(legacy).expect("legacy unknown extras must remain readable");
        let reserialized = serde_json::to_value(&legacy).expect("must serialize");
        for key in ["deberta_score", "content_threat"] {
            assert!(
                reserialized.get(key).is_none(),
                "legacy {key} leaked back into current output: {reserialized}"
            );
        }
    }
}
