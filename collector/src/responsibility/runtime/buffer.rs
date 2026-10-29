// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// buffer.rs — Thin GraphDeltaBuffer (Vec accumulator).
//
// Per spec §15, the runtime's buffer is a minimal accumulator that collects
// nodes, edges, evidence, and timeline entries for batch processing during
// trigger(). Unlike the legacy buffer.rs, this is intentionally simple:
// no compression, no incremental snapshots, no behavior timeline generation.
//
// The classification pipeline reads from this buffer during each trigger()
// invocation, then clears it.

use crate::responsibility::types::alert_types::BehaviorTimelineEntry;
use crate::responsibility::types::evidence_types::{Action, CorrelationEvidence, Intent};
use crate::responsibility::types::graph_types::{ResponsibilityEdge, ResponsibilityNode};
use crate::responsibility::types::ids::{ActionId, IntentId};

// ─── GraphDeltaBuffer ──────────────────────────────────────────────────

/// Thin accumulator for graph deltas, evidence, and timeline entries.
///
/// Accumulates data between trigger() invocations. Each trigger() drains
/// the buffer and processes accumulated items through the full pipeline.
#[derive(Debug, Clone, Default)]
pub struct GraphDeltaBuffer {
    /// Accumulated graph nodes.
    pub nodes: Vec<ResponsibilityNode>,
    /// Accumulated graph edges.
    pub edges: Vec<ResponsibilityEdge>,
    /// Accumulated intents (for graph builder lookups).
    pub intents: Vec<Intent>,
    /// Accumulated actions (for graph builder lookups).
    pub actions: Vec<Action>,
    /// Accumulated correlation evidence.
    pub evidence: Vec<CorrelationEvidence>,
    /// Behavior timeline entries (ordered by time).
    pub timeline: Vec<BehaviorTimelineEntry>,
    /// Content signatures of intents already accepted.
    ///
    /// An LLM request carries the whole conversation history, so every turn
    /// re-yields the tool calls and prompts of all earlier turns. Without
    /// this, the same logical intent is extracted once per subsequent
    /// request, and every downstream artifact (evidence, claims, alerts)
    /// is multiplied along with it.
    seen_intent_signatures: std::collections::HashSet<u64>,
}

impl GraphDeltaBuffer {
    /// Create an empty buffer.
    pub fn new() -> Self {
        Self::default()
    }

    /// Push a graph node into the buffer.
    pub fn push_node(&mut self, node: ResponsibilityNode) {
        self.nodes.push(node);
    }

    /// Push a graph edge into the buffer.
    pub fn push_edge(&mut self, edge: ResponsibilityEdge) {
        self.edges.push(edge);
    }

    /// Push an intent into the buffer.
    ///
    /// Unconditional: the runtime re-pushes intents after draining them, so
    /// this must not filter. Use [`Self::accept_intent_once`] at the point of
    /// extraction to suppress re-observations of the same content.
    pub fn push_intent(&mut self, intent: Intent) {
        self.intents.push(intent);
    }

    /// Record an intent's content signature.
    ///
    /// Returns `true` for a new process/context/native-reference/content
    /// combination, `false` for its re-observation. Callers should skip the
    /// intent when this returns `false`. The signature deliberately excludes
    /// timestamp and message id: those differ on every re-extraction, which is
    /// exactly why the existing id-based dedup never fired.
    pub fn accept_intent_once(&mut self, intent: &Intent) -> bool {
        self.seen_intent_signatures
            .insert(intent_content_signature(intent))
    }

    /// Push an action into the buffer.
    pub fn push_action(&mut self, action: Action) {
        self.actions.push(action);
    }

    /// Push correlation evidence into the buffer.
    pub fn push_evidence(&mut self, ev: CorrelationEvidence) {
        self.evidence.push(ev);
    }

    /// Push a timeline entry.
    pub fn push_timeline(&mut self, entry: BehaviorTimelineEntry) {
        self.timeline.push(entry);
    }

    /// Total node count.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Total edge count.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Total evidence count.
    pub fn evidence_count(&self) -> usize {
        self.evidence.len()
    }

    /// Whether the buffer has any accumulated items.
    pub fn has_pending(&self) -> bool {
        !self.nodes.is_empty()
            || !self.edges.is_empty()
            || !self.evidence.is_empty()
            || !self.timeline.is_empty()
    }

    /// Look up an intent by its ID.
    pub fn find_intent_by_id(&self, intent_id: &IntentId) -> Option<&Intent> {
        self.intents.iter().find(|i| i.intent_id == *intent_id)
    }

    /// Look up an action by its ID.
    pub fn find_action_by_id(&self, action_id: &ActionId) -> Option<&Action> {
        self.actions.iter().find(|a| a.action_id == *action_id)
    }

    /// Clear all accumulated data. Called after each trigger().
    pub fn clear(&mut self) {
        self.nodes.clear();
        self.edges.clear();
        self.intents.clear();
        self.actions.clear();
        self.evidence.clear();
        self.timeline.clear();
    }
}

/// Stable content signature of an intent.
///
/// Uses the owning process plus the tool name and normalised arguments — the
/// parts that identify *which agent intended to do what*. Timestamps and
/// freshly generated ids are excluded on purpose, but the process identity is
/// not: two agents that run the same `Bash {command:"ls"}` are two distinct
/// intents, and dropping the second would erase one agent's attribution.
fn intent_content_signature(intent: &Intent) -> u64 {
    use std::hash::{Hash, Hasher};

    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    intent.process_id.pid.hash(&mut hasher);
    intent.process_id.start_time_ns.hash(&mut hasher);
    intent.tool_name.hash(&mut hasher);
    match serde_json::to_string(&intent.tool_args) {
        Ok(serialised) => serialised.hash(&mut hasher),
        Err(_) => intent.tool_args.len().hash(&mut hasher),
    }
    // Captured native references and declared sessions distinguish two
    // genuinely separate observations of the same tool name and arguments.
    // `None` stays a bounded legacy content-only signature: it does not prove
    // one execution, only that the capture offered no distinguishing anchor.
    if let Some(native) = &intent.native_tool_call {
        native.format.hash(&mut hasher);
        native.id.hash(&mut hasher);
    }
    intent.declared_conversation_id.hash(&mut hasher);
    hasher.finish()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::evidence_types::IntentAuthoritySource;
    use crate::responsibility::types::evidence_types::{
        Intent, IntentClass, IntentParseState, NativeToolCallFormat, NativeToolCallRef,
    };
    use crate::responsibility::types::ids::{IntentId, MessageId, ProcessId};

    fn make_intent(tool: &str, ts: u64) -> Intent {
        Intent {
            intent_id: IntentId::new(),
            process_id: ProcessId::new(1, 0),
            message_id: MessageId::new(),
            tool_name: tool.to_string(),
            tool_args: Vec::new(),
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::SseResponseToolUse,
            timestamp_ns: ts,
            native_tool_call: None,
            declared_conversation_id: None,
        }
    }

    #[test]
    fn accept_intent_once_rejects_repeat_of_same_content() {
        let mut buf = GraphDeltaBuffer::new();
        // Same tool name, different ids and timestamps — exactly what
        // re-extracting the request history produces.
        assert!(
            buf.accept_intent_once(&make_intent("exec", 100)),
            "first sight accepted"
        );
        assert!(
            !buf.accept_intent_once(&make_intent("exec", 200)),
            "later sight rejected"
        );
        assert!(
            !buf.accept_intent_once(&make_intent("exec", 300)),
            "still rejected"
        );
    }

    #[test]
    fn accept_intent_once_allows_distinct_content() {
        let mut buf = GraphDeltaBuffer::new();
        assert!(buf.accept_intent_once(&make_intent("exec", 1)));
        assert!(
            buf.accept_intent_once(&make_intent("ls", 1)),
            "different tool is distinct"
        );
    }

    fn with_native(mut intent: Intent, id: &str) -> Intent {
        intent.native_tool_call = Some(NativeToolCallRef {
            format: NativeToolCallFormat::Anthropic,
            id: id.into(),
        });
        intent
    }

    fn with_conversation(mut intent: Intent, id: &str) -> Intent {
        intent.declared_conversation_id = Some(id.into());
        intent
    }

    #[test]
    fn distinct_native_ids_with_identical_arguments_are_both_kept() {
        let mut buf = GraphDeltaBuffer::new();
        assert!(buf.accept_intent_once(&with_native(make_intent("Bash", 1), "toolu_a")));
        assert!(
            buf.accept_intent_once(&with_native(make_intent("Bash", 2), "toolu_b")),
            "a different captured native call is not a repeat"
        );
    }

    #[test]
    fn same_reference_repeat_is_suppressed() {
        let mut buf = GraphDeltaBuffer::new();
        assert!(buf.accept_intent_once(&with_native(make_intent("Bash", 1), "toolu_a")));
        assert!(
            !buf.accept_intent_once(&with_native(make_intent("Bash", 2), "toolu_a")),
            "re-observing the same native reference is a repeat"
        );
    }

    #[test]
    fn distinct_declared_conversations_with_identical_content_are_both_kept() {
        let mut buf = GraphDeltaBuffer::new();
        assert!(buf.accept_intent_once(&with_conversation(make_intent("UserPrompt", 1), "conv-a")));
        assert!(
            buf.accept_intent_once(&with_conversation(make_intent("UserPrompt", 2), "conv-b")),
            "same process and content in another declared session is distinct"
        );
    }
}
