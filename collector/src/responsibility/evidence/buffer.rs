// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// buffer.rs — TTL-based buffers for streaming intent-to-action correlation.
//
// IntentBuffer and ActionBuffer are ring-buffer-like structures backed by
// VecDeque with time-to-live (TTL) eviction. They hold the working set of
// recently observed intents and actions for Phase 1 streaming correlation.
//
// Defaults:
//   max_size: 10_000 items
//   ttl_ns: 120_000_000_000 (120 seconds)

use crate::responsibility::types::{Action, ActionId, Intent, IntentId};
use std::collections::{HashSet, VecDeque};

// ─── Defaults ─────────────────────────────────────────────────────────

const DEFAULT_MAX_SIZE: usize = 10_000;
const DEFAULT_TTL_NS: u64 = 120_000_000_000; // 120 seconds

// ─── IntentBuffer ─────────────────────────────────────────────────────

/// TTL-bounded buffer of recently seen Intents.
///
/// Used by `EvidenceCorrelator` to hold intents that may match
/// incoming actions in the streaming Phase 1 path.
#[derive(Debug, Clone)]
pub struct IntentBuffer {
    items: VecDeque<Intent>,
    // Fast lookup: tracks which intent IDs are currently buffered
    ids: HashSet<IntentId>,
    max_size: usize,
    ttl_ns: u64,
}

impl IntentBuffer {
    pub fn new() -> Self {
        Self {
            items: VecDeque::new(),
            ids: HashSet::new(),
            max_size: DEFAULT_MAX_SIZE,
            ttl_ns: DEFAULT_TTL_NS,
        }
    }

    /// Create a buffer with custom capacity and TTL.
    pub fn with_config(max_size: usize, ttl_ns: u64) -> Self {
        Self {
            items: VecDeque::new(),
            ids: HashSet::new(),
            max_size,
            ttl_ns,
        }
    }

    /// Push an intent into the buffer.
    ///
    /// Evicts expired items first, then removes the oldest item if the
    /// buffer is at capacity.
    pub fn push(&mut self, intent: Intent) {
        let now_ns = intent.timestamp_ns;
        self.evict_expired(now_ns);
        self.evict_oldest_if_full();

        self.ids.insert(intent.intent_id.clone());
        self.items.push_back(intent);
    }

    /// Evict all intents older than `now_ns - ttl_ns`.
    pub fn evict_expired(&mut self, now_ns: u64) {
        let cutoff = now_ns.saturating_sub(self.ttl_ns);
        while let Some(front) = self.items.front() {
            if front.timestamp_ns < cutoff {
                let removed = self.items.pop_front().unwrap();
                self.ids.remove(&removed.intent_id);
            } else {
                break;
            }
        }
    }

    /// Remove the oldest intent if the buffer has reached max capacity.
    fn evict_oldest_if_full(&mut self) {
        while self.items.len() >= self.max_size {
            if let Some(removed) = self.items.pop_front() {
                self.ids.remove(&removed.intent_id);
            }
        }
    }

    /// Iterate over all buffered intents (immutable borrow).
    pub fn iter(&self) -> impl Iterator<Item = &Intent> {
        self.items.iter()
    }

    /// Number of items currently in the buffer.
    pub fn len(&self) -> usize {
        self.items.len()
    }

    /// Whether the buffer is empty.
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// Drain all items from the buffer (for batch processing).
    pub fn drain_all(&mut self) -> Vec<Intent> {
        self.ids.clear();
        self.items.drain(..).collect()
    }

    /// Get a reference to the buffer contents.
    pub fn as_slice(&self) -> &VecDeque<Intent> {
        &self.items
    }
}

impl Default for IntentBuffer {
    fn default() -> Self {
        Self::new()
    }
}

// ─── ActionBuffer ─────────────────────────────────────────────────────

/// TTL-bounded buffer of recently seen Actions.
///
/// Used by `EvidenceCorrelator` to hold actions that may match
/// incoming intents in the streaming Phase 1 path.
#[derive(Debug, Clone)]
pub struct ActionBuffer {
    items: VecDeque<Action>,
    // Fast lookup: tracks which action IDs are currently buffered
    ids: HashSet<ActionId>,
    max_size: usize,
    ttl_ns: u64,
}

impl ActionBuffer {
    pub fn new() -> Self {
        Self {
            items: VecDeque::new(),
            ids: HashSet::new(),
            max_size: DEFAULT_MAX_SIZE,
            ttl_ns: DEFAULT_TTL_NS,
        }
    }

    /// Create a buffer with custom capacity and TTL.
    pub fn with_config(max_size: usize, ttl_ns: u64) -> Self {
        Self {
            items: VecDeque::new(),
            ids: HashSet::new(),
            max_size,
            ttl_ns,
        }
    }

    /// Push an action into the buffer.
    ///
    /// Evicts expired items first, then removes the oldest item if the
    /// buffer is at capacity.
    pub fn push(&mut self, action: Action) {
        let now_ns = action.timestamp_ns;
        self.evict_expired(now_ns);
        self.evict_oldest_if_full();

        self.ids.insert(action.action_id.clone());
        self.items.push_back(action);
    }

    /// Evict all actions older than `now_ns - ttl_ns`.
    pub fn evict_expired(&mut self, now_ns: u64) {
        let cutoff = now_ns.saturating_sub(self.ttl_ns);
        while let Some(front) = self.items.front() {
            if front.timestamp_ns < cutoff {
                let removed = self.items.pop_front().unwrap();
                self.ids.remove(&removed.action_id);
            } else {
                break;
            }
        }
    }

    /// Remove the oldest action if the buffer has reached max capacity.
    fn evict_oldest_if_full(&mut self) {
        while self.items.len() >= self.max_size {
            if let Some(removed) = self.items.pop_front() {
                self.ids.remove(&removed.action_id);
            }
        }
    }

    /// Iterate over all buffered actions (immutable borrow).
    pub fn iter(&self) -> impl Iterator<Item = &Action> {
        self.items.iter()
    }

    /// Number of items currently in the buffer.
    pub fn len(&self) -> usize {
        self.items.len()
    }

    /// Whether the buffer is empty.
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// Drain all items from the buffer (for batch processing).
    pub fn drain_all(&mut self) -> Vec<Action> {
        self.ids.clear();
        self.items.drain(..).collect()
    }

    /// Get a reference to the buffer contents.
    pub fn as_slice(&self) -> &VecDeque<Action> {
        &self.items
    }
}

impl Default for ActionBuffer {
    fn default() -> Self {
        Self::new()
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        ActionCandidateType, CandidateDerivationMode, IntentAuthoritySource, IntentClass,
        IntentParseState, MessageId, NormalizedArg, ProcessId, SignalClass,
    };

    fn pid() -> ProcessId {
        ProcessId::new(100, 1)
    }

    fn msg_id() -> MessageId {
        MessageId::from("msg-001")
    }

    fn make_intent(ts: u64) -> Intent {
        Intent {
            intent_id: IntentId::new(),
            process_id: pid(),
            message_id: msg_id(),
            tool_name: "Bash".to_string(),
            tool_args: vec![NormalizedArg::Command("ls".into())],
            intent_class: IntentClass::Orchestration,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::SseResponseToolUse,
            timestamp_ns: ts,
        }
    }

    fn make_action(ts: u64) -> Action {
        Action {
            action_id: ActionId::new(),
            process_id: pid(),
            action_type: ActionCandidateType::FileRead,
            normalized_args: vec![NormalizedArg::Path("/etc/hosts".into())],
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: ts,
        }
    }

    #[test]
    fn test_intent_buffer_push_and_iter() {
        let mut buf = IntentBuffer::new();
        let i1 = make_intent(1000);
        let i2 = make_intent(2000);

        buf.push(i1.clone());
        buf.push(i2.clone());

        assert_eq!(buf.len(), 2);
        let items: Vec<_> = buf.iter().collect();
        assert_eq!(items.len(), 2);
        assert_eq!(items[0].timestamp_ns, 1000);
        assert_eq!(items[1].timestamp_ns, 2000);
    }

    #[test]
    fn test_intent_buffer_ttl_eviction() {
        let mut buf = IntentBuffer::with_config(100, 50_000); // 50us TTL

        buf.push(make_intent(1000));
        buf.push(make_intent(2000));

        assert_eq!(buf.len(), 2);

        // Evict at t=52000: item at 1000 is expired, 2000 is not
        buf.evict_expired(52000);
        assert_eq!(buf.len(), 1);
        assert_eq!(buf.iter().next().unwrap().timestamp_ns, 2000);
    }

    #[test]
    fn test_intent_buffer_max_size_eviction() {
        let mut buf = IntentBuffer::with_config(3, 1_000_000_000);

        buf.push(make_intent(1));
        buf.push(make_intent(2));
        buf.push(make_intent(3));
        assert_eq!(buf.len(), 3);

        // Push #4: should evict #1
        buf.push(make_intent(4));
        assert_eq!(buf.len(), 3);
        // First item should be #2
        assert_eq!(buf.iter().next().unwrap().timestamp_ns, 2);
    }

    #[test]
    fn test_intent_buffer_drain_all() {
        let mut buf = IntentBuffer::new();
        buf.push(make_intent(1));
        buf.push(make_intent(2));

        let drained = buf.drain_all();
        assert_eq!(drained.len(), 2);
        assert!(buf.is_empty());
        assert_eq!(buf.len(), 0);
    }

    #[test]
    fn test_action_buffer_push_and_evict() {
        let mut buf = ActionBuffer::with_config(5, 10_000);

        buf.push(make_action(1000));
        buf.push(make_action(5000));

        assert_eq!(buf.len(), 2);

        // Evict at t=12000: item at 1000 is expired
        buf.evict_expired(12000);
        assert_eq!(buf.len(), 1);
    }

    #[test]
    fn test_action_buffer_max_capacity() {
        let mut buf = ActionBuffer::with_config(2, 1_000_000);

        buf.push(make_action(1));
        buf.push(make_action(2));
        assert_eq!(buf.len(), 2);

        buf.push(make_action(3));
        assert_eq!(buf.len(), 2);
    }

    #[test]
    fn test_action_buffer_drain() {
        let mut buf = ActionBuffer::new();
        buf.push(make_action(100));
        buf.push(make_action(200));

        let drained = buf.drain_all();
        assert_eq!(drained.len(), 2);
        assert!(buf.is_empty());
    }

    #[test]
    fn test_intent_buffer_default_ttl() {
        let mut buf = IntentBuffer::new();
        assert_eq!(buf.len(), 0);

        buf.push(make_intent(0));
        assert_eq!(buf.len(), 1);

        // Evict at the TTL boundary (120s)
        buf.evict_expired(120_000_000_000);
        assert_eq!(buf.len(), 1); // 0 is still within default 120s window? Let's recheck

        // At exactly 120_000_000_000ns: cutoff = 120_000_000_000 - 120_000_000_000 = 0
        // Item at timestamp_ns=0 is NOT less than cutoff (0), so it survives
        buf.evict_expired(120_000_000_001);
        assert_eq!(buf.len(), 0); // Now it's expired
    }

    #[test]
    fn test_action_buffer_as_slice() {
        let mut buf = ActionBuffer::new();
        buf.push(make_action(1000));
        buf.push(make_action(2000));

        let slice = buf.as_slice();
        assert_eq!(slice.len(), 2);
    }

    #[test]
    fn test_intent_buffer_is_empty() {
        let buf = IntentBuffer::new();
        assert!(buf.is_empty());
        assert!(buf.iter().next().is_none());
    }
}
