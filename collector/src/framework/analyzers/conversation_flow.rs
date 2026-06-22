// SPDX-License-Identifier: MIT
// Copyright (c) 2026 AgentSight Project

//! Agent Conversation Flow Analyzer (ACFA-001, Phase A)
//!
//! Tracks multi-turn agent conversations by consuming `causal_link` events
//! and maintaining a bounded LRU cache of `ConversationState` entries.
//!
//! ## Architecture
//!
//! ```text
//! Event Stream (causal_link, responsibility)
//!     │
//!     ▼
//! ConversationFlowAnalyzer
//!     ├── Extract message_id from event data
//!     ├── Create or update ConversationState
//!     ├── Compute health score
//!     ├── Check for stall transitions
//!     ├── LRU eviction when at capacity
//!     └── Emit conversation_flow events
//!     │
//!     ▼
//! Event Stream (pass-through + conversation_flow events)
//! ```
//!
//! ## Design Decisions
//!
//! - **DDR-001**: Dedicated analyzer (not inline in CausalTraceAnalyzer)
//! - **DDR-002**: LRU HashMap for bounded memory (default 10,000 conversations)
//! - **DDR-003**: Non-blocking design with RwLock for concurrent access
//! - **DDR-004**: Events emitted with `source: conversation_flow`
//! - **DDR-005**: Health score decays linearly: 1.0 → 0.0 over stall_timeout
//!
//! ## Phase A Scope
//!
//! Phase A implements the core state machine (active ↔ stalled, active → completed),
//! health score computation, LRU eviction, and basic event extraction. Stall
//! detection is present but operates on event-driven evaluation (periodic check
//! is Phase C). Metrics aggregation and API endpoints are Phase B.

use super::{Analyzer, AnalyzerError};
use crate::framework::analyzers::pipeline_contract::{PipelineStage, StageMetrics};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use async_trait::async_trait;
use futures::stream::{self, StreamExt};
use serde_json::json;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::Duration;

// ─────────────────────────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────────────────────────

/// Configuration for `ConversationFlowAnalyzer`.
///
/// All durations are in nanoseconds internally for consistency with the
/// nanosecond timestamps in the `Event` struct.
#[derive(Debug, Clone)]
pub struct ConversationFlowConfig {
    /// Maximum number of conversations to track in memory.
    /// When exceeded, the oldest (by last_turn_at) is evicted.
    pub max_capacity: usize,
    /// TTL for conversations with no activity (nanoseconds).
    /// Conversations idle for longer than this are eligible for eviction.
    pub ttl_ns: u64,
    /// Time without a turn before a conversation is considered stalled (ns).
    pub stall_timeout_ns: u64,
    /// Grace period after conversation creation before stall detection (ns).
    pub grace_period_ns: u64,
}

impl Default for ConversationFlowConfig {
    fn default() -> Self {
        Self {
            max_capacity: 10_000,
            ttl_ns: Duration::from_secs(30 * 60).as_nanos() as u64,     // 30 minutes
            stall_timeout_ns: Duration::from_secs(300).as_nanos() as u64, // 5 minutes
            grace_period_ns: Duration::from_secs(30).as_nanos() as u64,  // 30 seconds
        }
    }
}

impl ConversationFlowConfig {
    /// Set the maximum number of conversations.
    pub fn with_max_capacity(mut self, capacity: usize) -> Self {
        self.max_capacity = capacity;
        self
    }

    /// Set the TTL for conversation inactivity.
    pub fn with_ttl(mut self, ttl: Duration) -> Self {
        self.ttl_ns = ttl.as_nanos() as u64;
        self
    }

    /// Set the stall timeout.
    pub fn with_stall_timeout(mut self, timeout: Duration) -> Self {
        self.stall_timeout_ns = timeout.as_nanos() as u64;
        self
    }

    /// Set the grace period for new conversations.
    pub fn with_grace_period(mut self, grace: Duration) -> Self {
        self.grace_period_ns = grace.as_nanos() as u64;
        self
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Conversation State Types
// ─────────────────────────────────────────────────────────────────────────────

/// Unique identifier for a conversation (matches `message_id` from events).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ConversationId(pub String);

/// Status of a multi-turn conversation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConversationStatus {
    Active,
    Stalled,
    Completed,
    Expired,
}

impl ConversationStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            ConversationStatus::Active => "active",
            ConversationStatus::Stalled => "stalled",
            ConversationStatus::Completed => "completed",
            ConversationStatus::Expired => "expired",
        }
    }
}

/// Tracks the state of a multi-turn conversation between agents.
#[derive(Debug, Clone)]
pub struct ConversationState {
    pub id: ConversationId,
    pub agent_id_1: Option<String>,
    pub agent_id_2: Option<String>,
    pub status: ConversationStatus,
    pub turn_count: u64,
    pub stall_count: u64,
    pub health_score: f64,
    pub created_at_ns: u64,
    pub last_turn_at_ns: u64,
    pub completed_at_ns: Option<u64>,
    pub stalled_at_ns: Option<u64>,
}

impl ConversationState {
    pub fn new(
        id: ConversationId,
        agent_id_1: Option<String>,
        agent_id_2: Option<String>,
        created_at_ns: u64,
    ) -> Self {
        Self {
            id,
            agent_id_1,
            agent_id_2,
            status: ConversationStatus::Active,
            turn_count: 1,
            stall_count: 0,
            health_score: 1.0,
            created_at_ns,
            last_turn_at_ns: created_at_ns,
            completed_at_ns: None,
            stalled_at_ns: None,
        }
    }

    pub fn record_turn(&mut self, timestamp_ns: u64, stall_timeout_ns: u64) {
        self.turn_count += 1;
        self.last_turn_at_ns = timestamp_ns;
        // Recover from stall if stalled
        if self.status == ConversationStatus::Stalled {
            self.status = ConversationStatus::Active;
        }
        self.refresh_health_score(timestamp_ns, stall_timeout_ns);
    }

    pub fn mark_completed(&mut self, timestamp_ns: u64) {
        self.status = ConversationStatus::Completed;
        self.completed_at_ns = Some(timestamp_ns);
    }

    pub fn mark_expired(&mut self) {
        self.status = ConversationStatus::Expired;
    }

    /// Check if conversation has stalled. Returns true if transition occurred.
    pub fn check_stall(
        &mut self,
        now_ns: u64,
        stall_timeout_ns: u64,
        grace_period_ns: u64,
    ) -> bool {
        if self.status != ConversationStatus::Active {
            return false;
        }
        // Within grace period -> no stall
        if now_ns.saturating_sub(self.created_at_ns) < grace_period_ns {
            return false;
        }
        let idle_ns = now_ns.saturating_sub(self.last_turn_at_ns);
        if idle_ns >= stall_timeout_ns {
            self.status = ConversationStatus::Stalled;
            self.stall_count += 1;
            self.stalled_at_ns = Some(now_ns);
            true
        } else {
            false
        }
    }

    /// Compute the health score (0.0-1.0) based on idle time.
    pub fn compute_health_score(&self, now_ns: u64, stall_timeout_ns: u64) -> f64 {
        if self.status != ConversationStatus::Active {
            return 0.0;
        }
        let idle_ns = now_ns.saturating_sub(self.last_turn_at_ns);
        if idle_ns >= stall_timeout_ns {
            return 0.0;
        }
        if stall_timeout_ns == 0 {
            return 1.0;
        }
        let ratio = (idle_ns as f64) / (stall_timeout_ns as f64);
        (1.0 - ratio).max(0.0)
    }

    /// Compute and store the health score.
    pub fn refresh_health_score(&mut self, now_ns: u64, stall_timeout_ns: u64) -> f64 {
        let score = self.compute_health_score(now_ns, stall_timeout_ns);
        self.health_score = score;
        score
    }

    /// Return the idle time (nanoseconds since last turn).
    pub fn idle_time_ns(&self, now_ns: u64) -> u64 {
        now_ns.saturating_sub(self.last_turn_at_ns)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal: Eviction Tracker
// ─────────────────────────────────────────────────────────────────────────────

/// Tracks which conversations are eligible for eviction.
struct EvictionTracker {
    /// Ordered list of conversation IDs by last_turn_at (oldest first).
    /// Used for O(1) LRU eviction target lookup.
    lru_order: Vec<ConversationId>,
}

impl EvictionTracker {
    fn new() -> Self {
        Self {
            lru_order: Vec::with_capacity(100),
        }
    }

    /// Record that a conversation was touched/updated.
    /// Moves it to the end of the LRU order.
    fn touch(&mut self, id: &ConversationId) {
        // Remove if present
        if let Some(pos) = self.lru_order.iter().position(|x| x == id) {
            self.lru_order.remove(pos);
        }
        self.lru_order.push(id.clone());
    }

    /// Get the oldest (least recently used) conversation ID.
    fn oldest(&self) -> Option<&ConversationId> {
        self.lru_order.first()
    }

    /// Remove a conversation from the tracker.
    fn remove(&mut self, id: &ConversationId) {
        if let Some(pos) = self.lru_order.iter().position(|x| x == id) {
            self.lru_order.remove(pos);
        }
    }

    /// Number of tracked conversations.
    fn len(&self) -> usize {
        self.lru_order.len()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Analyzer
// ─────────────────────────────────────────────────────────────────────────────

/// Analyzer that tracks multi-turn agent conversation state.
///
/// ## Event Consumption
///
/// Consumes events with `source` matching `causal_link`.
/// Extracts the `message_id` and optionally agent IDs from event data.
///
/// ## Event Production
///
/// Produces events with `source: conversation_flow` on:
/// - First turn detected (`sub_type: conversation_created`)
/// - Subsequent turn detected (`sub_type: conversation_turn`)
/// - State transition to stalled (`sub_type: conversation_stalled`)
/// - State transition to completed (`sub_type: conversation_completed`)
///
/// All input events pass through unchanged.
pub struct ConversationFlowAnalyzer {
    /// Configuration parameters.
    config: ConversationFlowConfig,
    /// Active conversation state map.
    conversations: Arc<RwLock<HashMap<ConversationId, ConversationState>>>,
    /// LRU eviction order tracking.
    eviction: Arc<RwLock<EvictionTracker>>,
    /// Total conversations evicted since start.
    evicted_count: Arc<std::sync::atomic::AtomicU64>,
    /// Total conversations completed since start.
    completed_count: Arc<std::sync::atomic::AtomicU64>,
    /// Total events processed by this analyzer.
    events_processed: Arc<std::sync::atomic::AtomicU64>,
    /// Analyzer name.
    name: String,
}

impl ConversationFlowAnalyzer {
    /// Create a new `ConversationFlowAnalyzer` with default configuration.
    pub fn new() -> Self {
        Self::with_config(ConversationFlowConfig::default())
    }

    /// Create a new `ConversationFlowAnalyzer` with custom configuration.
    pub fn with_config(config: ConversationFlowConfig) -> Self {
        let max_capacity = config.max_capacity;
        Self {
            config,
            conversations: Arc::new(RwLock::new(HashMap::with_capacity(max_capacity))),
            eviction: Arc::new(RwLock::new(EvictionTracker::new())),
            evicted_count: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            completed_count: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            events_processed: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            name: "ConversationFlowAnalyzer".to_string(),
        }
    }

    /// Process a single event through the conversation flow analyzer.
    ///
    /// Returns the original event (pass-through) plus any derived
    /// conversation_flow events.
    fn process_single_event(&self, event: Event) -> Vec<Event> {
        self.events_processed
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);

        let now_ns = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;

        // Only process causal_link events
        let is_conversation_event = event.source == "causal_link"
            || event.source == "causal_link_metrics";

        if !is_conversation_event {
            return vec![event];
        }

        // Extract message_id from event data
        let message_id = event
            .data
            .get("message_id")
            .and_then(|v| v.as_str())
            .map(|s| ConversationId(s.to_string()));

        let message_id = match message_id {
            Some(id) if !id.0.is_empty() => id,
            _ => return vec![event], // No message_id, pass through
        };

        // Extract agent IDs if present
        let agent_id_1 = event
            .data
            .get("agent_id_1")
            .or_else(|| event.data.get("agent_id"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let agent_id_2 = event
            .data
            .get("agent_id_2")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        // Also try extracting from agent_labels_resolved events
        let (agent_id_1, agent_id_2) = if agent_id_1.is_none() && agent_id_2.is_none() {
            Self::extract_agents_from_labels_resolved(&event)
        } else {
            (agent_id_1, agent_id_2)
        };

        // Check if this is a "completed" event (e.g., session end marker)
        let is_completion = event
            .data
            .get("type")
            .and_then(|v| v.as_str())
            .map(|t| t == "agent_labels_resolved" || t == "session_end")
            .unwrap_or(false);

        let mut derived_events = Vec::new();

        // Update conversation state
        let mut conversations = self.conversations.write().unwrap();
        let mut eviction = self.eviction.write().unwrap();

        if let Some(state) = conversations.get_mut(&message_id) {
            if is_completion {
                // Mark as completed
                state.mark_completed(now_ns);
                self.completed_count
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed);

                let completion_event = Event::new_with_timestamp(
                    now_ns,
                    "conversation_flow".to_string(),
                    event.pid,
                    event.comm.clone(),
                    json!({
                        "type": "conversation_flow",
                        "sub_type": "conversation_completed",
                        "message_id": message_id.0,
                        "total_turns": state.turn_count,
                        "health_score": state.health_score,
                        "stall_count": state.stall_count,
                    }),
                );
                derived_events.push(completion_event);
            } else {
                // Record a new turn
                state.record_turn(now_ns, self.config.stall_timeout_ns);

                let turn_event = Event::new_with_timestamp(
                    now_ns,
                    "conversation_flow".to_string(),
                    event.pid,
                    event.comm.clone(),
                    json!({
                        "type": "conversation_flow",
                        "sub_type": "conversation_turn",
                        "message_id": message_id.0,
                        "turn_count": state.turn_count,
                        "health_score": state.health_score,
                    }),
                );
                derived_events.push(turn_event);
            }

            // Update LRU order
            eviction.touch(&message_id);
        } else {
            // New conversation detected
            let mut state = ConversationState::new(
                message_id.clone(),
                agent_id_1,
                agent_id_2,
                now_ns,
            );

            if is_completion {
                // First event is already a completion — mark as completed immediately
                state.mark_completed(now_ns);
                self.completed_count
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }

            let creation_event = Event::new_with_timestamp(
                now_ns,
                "conversation_flow".to_string(),
                event.pid,
                event.comm.clone(),
                json!({
                    "type": "conversation_flow",
                    "sub_type": if is_completion { "conversation_completed" } else { "conversation_created" },
                    "message_id": message_id.0,
                    "turn_count": state.turn_count,
                    "status": state.status.as_str(),
                }),
            );
            derived_events.push(creation_event);

            // Check if we need to evict
            if conversations.len() >= self.config.max_capacity {
                Self::evict_one(&mut conversations, &mut eviction, &self.config, &self.evicted_count);
            }

            conversations.insert(message_id.clone(), state);
            eviction.touch(&message_id);
        }

        // -- Stall Check (evaluation on each event for Phase A) --
        // Phase C will add periodic stall detection via a tokio interval.
        // Phase A evaluates stall on each event to enable state machine testing.

        // We already hold the write lock on conversations, so check all active ones
        // for stall transitions periodically (every 100 events to avoid O(n) per event).
        let processed = self.events_processed.load(std::sync::atomic::Ordering::Relaxed);
        if processed % 100 == 0 {
            let stalled_ids: Vec<(ConversationId, u64)> = conversations
                .iter_mut()
                .filter_map(|(id, state)| {
                    if state.check_stall(now_ns, self.config.stall_timeout_ns, self.config.grace_period_ns) {
                        let duration_s = state.idle_time_ns(now_ns) / 1_000_000_000;
                        Some((id.clone(), duration_s))
                    } else {
                        None
                    }
                })
                .collect();

            for (stalled_id, duration_s) in stalled_ids {
                let stall_event = Event::new_with_timestamp(
                    now_ns,
                    "conversation_flow".to_string(),
                    0,
                    "conversation_flow_analyzer".to_string(),
                    json!({
                        "type": "conversation_flow",
                        "sub_type": "conversation_stalled",
                        "message_id": stalled_id.0,
                        "health_score": 0.0,
                        "stall_duration_seconds": duration_s,
                    }),
                );
                derived_events.push(stall_event);
            }
        }

        // Combine original event with derived events
        let mut result = vec![event];
        result.extend(derived_events);
        result
    }

    /// Extract agent IDs from `agent_labels_resolved` event data.
    fn extract_agents_from_labels_resolved(event: &Event) -> (Option<String>, Option<String>) {
        let type_val = event.data.get("type").and_then(|v| v.as_str());
        if type_val != Some("agent_labels_resolved") {
            return (None, None);
        }

        let agents = event.data.get("agents").and_then(|v| v.as_array());
        match agents {
            Some(agents_arr) if agents_arr.len() >= 2 => {
                let agent_1 = agents_arr[0]
                    .get("agent_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let agent_2 = agents_arr[1]
                    .get("agent_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                (agent_1, agent_2)
            }
            Some(agents_arr) if agents_arr.len() == 1 => {
                let agent_1 = agents_arr[0]
                    .get("agent_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                (agent_1, None)
            }
            _ => (None, None),
        }
    }

    /// Evict the oldest conversation (LRU) from the state map.
    fn evict_one(
        conversations: &mut HashMap<ConversationId, ConversationState>,
        eviction: &mut EvictionTracker,
        config: &ConversationFlowConfig,
        evicted_counter: &Arc<std::sync::atomic::AtomicU64>,
    ) {
        // First, try to evict expired (TTL) conversations
        let now_ns = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;

        let expired_ids: Vec<ConversationId> = conversations
            .iter()
            .filter(|(_, state)| {
                state.status == ConversationStatus::Active
                    && now_ns.saturating_sub(state.last_turn_at_ns) >= config.ttl_ns
            })
            .map(|(id, _)| id.clone())
            .take(10) // Batch evict up to 10 expired per call
            .collect();

        if !expired_ids.is_empty() {
            for id in &expired_ids {
                if let Some(state) = conversations.get_mut(id) {
                    state.mark_expired();
                }
                eviction.remove(id);
                evicted_counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
            return;
        }

        // Otherwise, evict the LRU oldest
        if let Some(oldest_id) = eviction.oldest().cloned() {
            if let Some(state) = conversations.get_mut(&oldest_id) {
                state.mark_expired();
            }
            conversations.remove(&oldest_id);
            eviction.remove(&oldest_id);
            evicted_counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }
    }

    // ── Public Metrics Accessors (for Phase B API) ──────────────────────────

    /// Get a snapshot of all conversation states.
    pub fn get_all_states(&self) -> HashMap<ConversationId, ConversationState> {
        let conversations = self.conversations.read().unwrap();
        // Refresh health scores before snapshot
        let now_ns = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;
        let stall_timeout_ns = self.config.stall_timeout_ns;

        let mut snapshot: HashMap<ConversationId, ConversationState> = 
            conversations.clone();
        for (_, state) in snapshot.iter_mut() {
            state.refresh_health_score(now_ns, stall_timeout_ns);
        }
        snapshot
    }

    /// Get a single conversation's state by ID.
    pub fn get_state(&self, id: &ConversationId) -> Option<ConversationState> {
        let conversations = self.conversations.read().unwrap();
        conversations.get(id).cloned().map(|mut state| {
            let now_ns = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos() as u64;
            state.refresh_health_score(now_ns, self.config.stall_timeout_ns);
            state
        })
    }

    /// Get the number of conversations in each status.
    pub fn get_status_counts(&self) -> (u64, u64, u64, u64) {
        let conversations = self.conversations.read().unwrap();
        let mut active = 0u64;
        let mut stalled = 0u64;
        let mut completed = 0u64;
        let mut expired = 0u64;

        for state in conversations.values() {
            match state.status {
                ConversationStatus::Active => active += 1,
                ConversationStatus::Stalled => stalled += 1,
                ConversationStatus::Completed => completed += 1,
                ConversationStatus::Expired => expired += 1,
            }
        }

        (active, stalled, completed, expired)
    }

    /// Get total conversations that have been tracked (including evicted).
    pub fn total_conversations(&self) -> u64 {
        let current = self.conversations.read().unwrap().len() as u64;
        let evicted = self.evicted_count.load(std::sync::atomic::Ordering::Relaxed);
        // Note: completed conversations are still in the map, so we don't
        // add completed_count separately (they're included in current).
        current + evicted
    }

    /// Get the average health score across all active conversations.
    pub fn avg_health_score(&self) -> f64 {
        let conversations = self.conversations.read().unwrap();
        let now_ns = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;
        let stall_timeout_ns = self.config.stall_timeout_ns;

        let mut total_score = 0.0f64;
        let mut count = 0u64;

        for state in conversations.values() {
            if state.status == ConversationStatus::Active {
                total_score += state.compute_health_score(now_ns, stall_timeout_ns);
                count += 1;
            }
        }

        if count == 0 {
            0.0
        } else {
            total_score / count as f64
        }
    }

    /// Get the total number of events processed by this analyzer.
    pub fn events_processed_count(&self) -> u64 {
        self.events_processed.load(std::sync::atomic::Ordering::Relaxed)
    }

    /// Get the number of evicted conversations.
    pub fn evicted_count(&self) -> u64 {
        self.evicted_count.load(std::sync::atomic::Ordering::Relaxed)
    }

    /// Get the number of completed conversations.
    pub fn completed_count(&self) -> u64 {
        self.completed_count.load(std::sync::atomic::Ordering::Relaxed)
    }
}

impl Default for ConversationFlowAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Analyzer for ConversationFlowAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        // Note: Since we use Arc<RwLock<>> for internal state, we can safely
        // share access across the stream. This analyzer is stateless at the
        // Analyzer trait level (state is in shared Arc).
        let config = self.config.clone();
        let conversations: Arc<RwLock<HashMap<ConversationId, ConversationState>>> =
            Arc::clone(&self.conversations);
        let eviction = Arc::clone(&self.eviction);
        let evicted_count = Arc::clone(&self.evicted_count);
        let completed_count = Arc::clone(&self.completed_count);
        let events_processed = Arc::clone(&self.events_processed);
        let name = self.name.clone();

        // Build a processing closure that the stream can use
        let mapped = stream.flat_map(move |event| {
            let now_ns = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos() as u64;

            events_processed.fetch_add(1, std::sync::atomic::Ordering::Relaxed);

            // Only process causal_link events
            let is_conversation_event = event.source == "causal_link"
                || event.source == "causal_link_metrics";

            if !is_conversation_event {
                return stream::iter(vec![event]);
            }

            let message_id = event
                .data
                .get("message_id")
                .and_then(|v| v.as_str())
                .map(|s| ConversationId(s.to_string()));

            let message_id = match message_id {
                Some(id) if !id.0.is_empty() => id,
                _ => return stream::iter(vec![event]),
            };

            let agent_id_1 = event
                .data
                .get("agent_id_1")
                .or_else(|| event.data.get("agent_id"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let agent_id_2 = event
                .data
                .get("agent_id_2")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let is_completion = event
                .data
                .get("type")
                .and_then(|v| v.as_str())
                .map(|t| t == "agent_labels_resolved" || t == "session_end")
                .unwrap_or(false);

            let mut derived_events = Vec::new();

            let mut conv_guard = conversations.write().unwrap();
            let mut evict_guard = eviction.write().unwrap();

            if let Some(state) = conv_guard.get_mut(&message_id) {
                if is_completion {
                    state.mark_completed(now_ns);
                    completed_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    derived_events.push(
                        Event::new_with_timestamp(
                            now_ns,
                            "conversation_flow".to_string(),
                            event.pid,
                            event.comm.clone(),
                            json!({
                                "type": "conversation_flow",
                                "sub_type": "conversation_completed",
                                "message_id": message_id.0,
                                "total_turns": state.turn_count,
                                "health_score": state.health_score,
                                "stall_count": state.stall_count,
                            }),
                        )
                    );
                } else {
                    state.record_turn(now_ns, config.stall_timeout_ns);
                    derived_events.push(
                        Event::new_with_timestamp(
                            now_ns,
                            "conversation_flow".to_string(),
                            event.pid,
                            event.comm.clone(),
                            json!({
                                "type": "conversation_flow",
                                "sub_type": "conversation_turn",
                                "message_id": message_id.0,
                                "turn_count": state.turn_count,
                                "health_score": state.health_score,
                            }),
                        )
                    );
                }
                evict_guard.touch(&message_id);
            } else {
                let mut state = ConversationState::new(
                    message_id.clone(),
                    agent_id_1,
                    agent_id_2,
                    now_ns,
                );

                if is_completion {
                    state.mark_completed(now_ns);
                    completed_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }

                if conv_guard.len() >= config.max_capacity {
                    // Evict expired first, then LRU
                    let expired_keys: Vec<ConversationId> = conv_guard
                        .iter()
                        .filter(|(_, s)| {
                            s.status == ConversationStatus::Active
                                && now_ns.saturating_sub(s.last_turn_at_ns) >= config.ttl_ns
                        })
                        .map(|(k, _)| k.clone())
                        .take(10)
                        .collect();

                    if !expired_keys.is_empty() {
                        for k in &expired_keys {
                            if let Some(s) = conv_guard.get_mut(k) {
                                s.mark_expired();
                            }
                            evict_guard.remove(k);
                            evicted_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        }
                    } else if let Some(oldest) = evict_guard.oldest().cloned() {
                        if let Some(s) = conv_guard.get_mut(&oldest) {
                            s.mark_expired();
                        }
                        conv_guard.remove(&oldest);
                        evict_guard.remove(&oldest);
                        evicted_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    }
                }

                derived_events.push(
                    Event::new_with_timestamp(
                        now_ns,
                        "conversation_flow".to_string(),
                        event.pid,
                        event.comm.clone(),
                        json!({
                            "type": "conversation_flow",
                            "sub_type": if is_completion { "conversation_completed" } else { "conversation_created" },
                            "message_id": message_id.0,
                            "turn_count": state.turn_count,
                            "status": state.status.as_str(),
                        }),
                    )
                );

                let cid = message_id.clone();
                conv_guard.insert(message_id, state);
                evict_guard.touch(&cid);
            }

            // Periodic stall check (every 100 events)
            let processed = events_processed.load(std::sync::atomic::Ordering::Relaxed);
            if processed % 100 == 0 {
                let stalled: Vec<(ConversationId, u64)> = conv_guard
                    .iter_mut()
                    .filter_map(|(id, state)| {
                        if state.check_stall(now_ns, config.stall_timeout_ns, config.grace_period_ns) {
                            let d = state.idle_time_ns(now_ns) / 1_000_000_000;
                            Some((id.clone(), d))
                        } else {
                            None
                        }
                    })
                    .collect();

                for (sid, sd) in stalled {
                    derived_events.push(
                        Event::new_with_timestamp(
                            now_ns,
                            "conversation_flow".to_string(),
                            0,
                            "conversation_flow_analyzer".to_string(),
                            json!({
                                "type": "conversation_flow",
                                "sub_type": "conversation_stalled",
                                "message_id": sid.0,
                                "health_score": 0.0,
                                "stall_duration_seconds": sd,
                            }),
                        )
                    );
                }
            }

            drop(conv_guard);
            drop(evict_guard);

            let mut result = vec![event];
            result.extend(derived_events);
            stream::iter(result)
        });

        Ok(Box::pin(mapped))
    }

    fn name(&self) -> &str {
        &self.name
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::core::Event;
    use futures::stream::StreamExt;
    use serde_json::json;
    use std::time::Duration;

    /// Helper to create a causal_link event with a message_id.
    fn make_causal_event(
        timestamp: u64,
        message_id: &str,
        pid: u32,
        sub_type: &str,
    ) -> Event {
        Event::new_with_timestamp(
            timestamp,
            "causal_link".to_string(),
            pid,
            "test-agent".to_string(),
            json!({
                "type": sub_type,
                "message_id": message_id,
                "intent": "test_intent",
            }),
        )
    }

    /// Helper to create an agent_labels_resolved event.
    fn make_completion_event(
        timestamp: u64,
        message_id: &str,
        agent_ids: Vec<&str>,
    ) -> Event {
        let agents: Vec<serde_json::Value> = agent_ids
            .iter()
            .map(|id| {
                json!({
                    "agent_id": id,
                    "process_count": 1,
                    "confidence": "Authoritative",
                })
            })
            .collect();

        Event::new_with_timestamp(
            timestamp,
            "causal_link".to_string(),
            0,
            "test-agent".to_string(),
            json!({
                "type": "agent_labels_resolved",
                "message_id": message_id,
                "agents": agents,
                "unresolved_count": 0,
            }),
        )
    }

    // ── State Machine Tests ─────────────────────────────────────────────────

    #[test]
    fn test_conversation_state_lifecycle_active_to_stalled() {
        let stall_timeout_ns = Duration::from_secs(300).as_nanos() as u64; // 5 min
        let grace_period_ns = Duration::from_secs(30).as_nanos() as u64;

        let mut state = ConversationState::new(
            ConversationId("conv_test".into()),
            Some("agent_a".into()),
            Some("agent_b".into()),
            1000,
        );

        assert_eq!(state.status, ConversationStatus::Active);
        assert_eq!(state.turn_count, 1);
        assert_eq!(state.health_score, 1.0);

        // Record a turn
        state.record_turn(2000, stall_timeout_ns);
        assert_eq!(state.turn_count, 2);
        assert_eq!(state.status, ConversationStatus::Active);
        assert_eq!(state.health_score, 1.0);

        // After stall_timeout has passed, check_stall should trigger
        let far_future = 2000 + stall_timeout_ns + 1;
        let stalled = state.check_stall(far_future, stall_timeout_ns, grace_period_ns);
        assert!(stalled, "Should detect stall after timeout");
        assert_eq!(state.status, ConversationStatus::Stalled);
        assert_eq!(state.stall_count, 1);

        // Recovery: new turn should transition back to active
        let recovery_time = far_future + 1;
        state.record_turn(recovery_time, stall_timeout_ns);
        assert_eq!(
            state.status,
            ConversationStatus::Active,
            "New turn should recover from stall"
        );
        assert_eq!(state.turn_count, 3);
        assert_eq!(state.health_score, 1.0);
    }

    #[test]
    fn test_conversation_state_completed() {
        let mut state = ConversationState::new(
            ConversationId("conv_test".into()),
            Some("agent_a".into()),
            None,
            1000,
        );

        assert_eq!(state.status, ConversationStatus::Active);

        state.mark_completed(2000);
        assert_eq!(state.status, ConversationStatus::Completed);

        // Completed conversations don't transition to stalled
        assert!(!state.check_stall(999999, 300_000_000_000, 30_000_000_000));
        assert_eq!(state.status, ConversationStatus::Completed);
    }

    #[test]
    fn test_conversation_state_expired() {
        let mut state = ConversationState::new(
            ConversationId("conv_test".into()),
            None,
            None,
            1000,
        );

        state.mark_expired();
        assert_eq!(state.status, ConversationStatus::Expired);
    }

    #[test]
    fn test_conversation_state_grace_period() {
        let stall_timeout_ns = Duration::from_secs(300).as_nanos() as u64;
        let grace_period_ns = Duration::from_secs(30).as_nanos() as u64;

        let mut state = ConversationState::new(
            ConversationId("conv_test".into()),
            Some("agent_a".into()),
            None,
            1000,
        );

        // Within grace period, stall should NOT trigger even if stall_timeout passed
        let within_grace = 1000 + 10_000_000_000; // 10 seconds after creation
        let stalled = state.check_stall(within_grace, stall_timeout_ns, grace_period_ns);
        assert!(!stalled, "Should not stall within grace period");
        assert_eq!(state.status, ConversationStatus::Active);

        // After grace period, stall should trigger
        let after_grace = 1000 + grace_period_ns + stall_timeout_ns + 1;
        let stalled = state.check_stall(after_grace, stall_timeout_ns, grace_period_ns);
        assert!(stalled, "Should stall after grace period + timeout");
    }

    #[test]
    fn test_health_score_computation() {
        let stall_timeout_ns = Duration::from_secs(300).as_nanos() as u64; // 5 min

        let state = ConversationState::new(
            ConversationId("conv_test".into()),
            None,
            None,
            1000,
        );

        // At the turn time, score should be 1.0
        let score = state.compute_health_score(1000, stall_timeout_ns);
        assert!((score - 1.0).abs() < 0.001);

        // Halfway through stall timeout
        let halfway_ns = 1000 + stall_timeout_ns / 2;
        let score = state.compute_health_score(halfway_ns, stall_timeout_ns);
        assert!((score - 0.5).abs() < 0.001);

        // At stall timeout boundary
        let boundary_ns = 1000 + stall_timeout_ns;
        let score = state.compute_health_score(boundary_ns, stall_timeout_ns);
        assert!((score - 0.0).abs() < 0.001);

        // Past stall timeout
        let past_ns = 1000 + stall_timeout_ns + 1;
        let score = state.compute_health_score(past_ns, stall_timeout_ns);
        assert!((score - 0.0).abs() < 0.001, "Score should floor at 0.0");

        // Refresh updates the stored value
        let mut mutable = ConversationState::new(
            ConversationId("conv_test2".into()),
            None,
            None,
            1000,
        );
        let refreshed = mutable.refresh_health_score(1000 + stall_timeout_ns / 2, stall_timeout_ns);
        assert!((refreshed - 0.5).abs() < 0.001);
        assert!((mutable.health_score - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_multiple_stalls_tracked() {
        let stall_timeout_ns = Duration::from_secs(300).as_nanos() as u64;
        let grace_period_ns = Duration::from_secs(30).as_nanos() as u64;

        let mut state = ConversationState::new(
            ConversationId("conv_test".into()),
            Some("agent_a".into()),
            Some("agent_b".into()),
            1000,
        );

        // Stall 1
        state.check_stall(1000 + stall_timeout_ns + 1, stall_timeout_ns, grace_period_ns);
        assert_eq!(state.stall_count, 1);

        // Recover
        state.record_turn(2000, stall_timeout_ns);
        assert_eq!(state.status, ConversationStatus::Active);

        // Stall 2
        state.check_stall(2000 + stall_timeout_ns + 1, stall_timeout_ns, grace_period_ns);
        assert_eq!(state.stall_count, 2);

        // Recover
        state.record_turn(3000, stall_timeout_ns);

        // Stall 3
        state.check_stall(3000 + stall_timeout_ns + 1, stall_timeout_ns, grace_period_ns);
        assert_eq!(state.stall_count, 3);
    }

    // ── Analyzer Integration Tests ──────────────────────────────────────────

    #[tokio::test]
    async fn test_analyzer_rejects_non_causal_events() {
        let mut analyzer = ConversationFlowAnalyzer::new();

        // A process event should pass through unchanged with no derived events
        let event = Event::new_with_timestamp(
            1000,
            "process".to_string(),
            100,
            "bash".to_string(),
            json!({"event": "EXEC", "filename": "/bin/ls"}),
        );

        let stream: EventStream = Box::pin(futures::stream::iter(vec![event.clone()]));
        let result = analyzer.process(stream).await.unwrap();
        let events: Vec<Event> = result.collect().await;

        assert_eq!(events.len(), 1, "Non-causal event should pass through unchanged");
        assert_eq!(events[0].source, "process");
    }

    #[tokio::test]
    async fn test_analyzer_creates_conversation_on_first_event() {
        let mut analyzer = ConversationFlowAnalyzer::new();

        let event = make_causal_event(1000, "conv_abc", 101, "intent");

        let stream: EventStream = Box::pin(futures::stream::iter(vec![event]));
        let result = analyzer.process(stream).await.unwrap();
        let events: Vec<Event> = result.collect().await;

        // Should have the original + conversation_created event
        assert_eq!(events.len(), 2, "Should produce original + created event");

        // Check the derived event
        let created = &events[1];
        assert_eq!(created.source, "conversation_flow");
        assert_eq!(
            created.data.get("sub_type").and_then(|v| v.as_str()),
            Some("conversation_created")
        );
        assert_eq!(
            created.data.get("message_id").and_then(|v| v.as_str()),
            Some("conv_abc")
        );
        assert_eq!(
            created.data.get("turn_count").and_then(|v| v.as_u64()),
            Some(1)
        );
    }

    #[tokio::test]
    async fn test_analyzer_tracks_turns() {
        let mut analyzer = ConversationFlowAnalyzer::new();

        let event1 = make_causal_event(1000, "conv_abc", 101, "intent");
        let event2 = make_causal_event(2000, "conv_abc", 102, "action_candidate");

        let stream: EventStream = Box::pin(futures::stream::iter(vec![event1, event2]));
        let result = analyzer.process(stream).await.unwrap();
        let events: Vec<Event> = result.collect().await;

        // Original events + created event + turn event = 4
        assert_eq!(events.len(), 4);

        // Check turn event
        let turn_event = &events[3];
        assert_eq!(turn_event.source, "conversation_flow");
        assert_eq!(
            turn_event.data.get("sub_type").and_then(|v| v.as_str()),
            Some("conversation_turn")
        );
        assert_eq!(
            turn_event.data.get("turn_count").and_then(|v| v.as_u64()),
            Some(2)
        );

        // Verify state via accessor
        let state = analyzer.get_state(&ConversationId("conv_abc".into()));
        assert!(state.is_some());
        assert_eq!(state.unwrap().turn_count, 2);
    }

    #[tokio::test]
    async fn test_analyzer_multiple_conversations() {
        let mut analyzer = ConversationFlowAnalyzer::new();

        let events = vec![
            make_causal_event(1000, "conv_001", 101, "intent"),
            make_causal_event(1001, "conv_002", 201, "intent"),
            make_causal_event(2000, "conv_001", 102, "action_candidate"),
            make_causal_event(2001, "conv_003", 301, "intent"),
        ];

        let stream: EventStream = Box::pin(futures::stream::iter(events));
        let result = analyzer.process(stream).await.unwrap();
        let all_events: Vec<Event> = result.collect().await;

        // Original 4 + 3 created + 1 turn = 8
        assert_eq!(all_events.len(), 8, "Should have 4 originals + 4 derived events");

        let (active, stalled, completed, expired) = analyzer.get_status_counts();
        assert_eq!(active, 3, "Should have 3 active conversations");
        assert_eq!(stalled, 0);
        assert_eq!(completed, 0);
        assert_eq!(expired, 0);
    }

    #[tokio::test]
    async fn test_analyzer_completion_event() {
        let mut analyzer = ConversationFlowAnalyzer::new();

        let events = vec![
            make_causal_event(1000, "conv_abc", 101, "intent"),
            make_completion_event(2000, "conv_abc", vec!["agent_a", "agent_b"]),
        ];

        let stream: EventStream = Box::pin(futures::stream::iter(events));
        let result = analyzer.process(stream).await.unwrap();
        let all_events: Vec<Event> = result.collect().await;

        // 2 originals + 1 created + 1 completed = 4
        assert_eq!(all_events.len(), 4);

        // Verify completed state
        let state = analyzer.get_state(&ConversationId("conv_abc".into()));
        assert!(state.is_some());
        assert_eq!(state.unwrap().status, ConversationStatus::Completed);
    }

    #[tokio::test]
    async fn test_analyzer_agent_id_extraction() {
        let mut analyzer = ConversationFlowAnalyzer::new();

        // An agent_labels_resolved event with agent IDs
        let event = make_completion_event(1000, "conv_agent_test", vec!["agent_a", "agent_b"]);

        let stream: EventStream = Box::pin(futures::stream::iter(vec![event]));
        let result = analyzer.process(stream).await.unwrap();
        let events: Vec<Event> = result.collect().await;

        // Should create conversation from labels_resolved event
        assert_eq!(events.len(), 2);

        let state = analyzer.get_state(&ConversationId("conv_agent_test".into()));
        assert!(state.is_some());
        let state = state.unwrap();
        // agent_labels_resolved creates the conversation as completed
        assert_eq!(state.status, ConversationStatus::Completed);
    }

    // ── LRU Eviction Tests ─────────────────────────────────────────────────

    #[tokio::test]
    async fn test_lru_eviction_at_capacity() {
        let config = ConversationFlowConfig::default().with_max_capacity(5);
        let mut analyzer = ConversationFlowAnalyzer::with_config(config);

        let mut events = Vec::new();
        for i in 0..6u64 {
            events.push(make_causal_event(
                1000 + i,
                &format!("conv_{:03}", i),
                (100 + i) as u32,
                "intent",
            ));
        }

        let stream: EventStream = Box::pin(futures::stream::iter(events));
        let result = analyzer.process(stream).await.unwrap();
        let _: Vec<Event> = result.collect().await;

        // Should have evicted the oldest conversation (conv_000)
        let state_oldest = analyzer.get_state(&ConversationId("conv_000".into()));
        assert!(state_oldest.is_none() || state_oldest.unwrap().status == ConversationStatus::Expired);

        // Latest (conv_005) should still be present
        let state_latest = analyzer.get_state(&ConversationId("conv_005".into()));
        assert!(state_latest.is_some());
        assert_eq!(state_latest.unwrap().status, ConversationStatus::Active);

        // Verify eviction count > 0
        assert!(analyzer.evicted_count() > 0);
    }

    #[tokio::test]
    async fn test_ttl_expiration_eviction() {
        // Use a very short TTL (100ms) for testing
        let config = ConversationFlowConfig::default()
            .with_max_capacity(100)
            .with_ttl(Duration::from_millis(100));
        let mut analyzer = ConversationFlowAnalyzer::with_config(config);

        // Create a conversation with an old timestamp
        let old_event = Event::new_with_timestamp(
            1000,
            "causal_link".to_string(),
            101,
            "test-agent".to_string(),
            json!({
                "type": "intent",
                "message_id": "conv_old",
            }),
        );

        let stream: EventStream = Box::pin(futures::stream::iter(vec![old_event]));
        let result = analyzer.process(stream).await.unwrap();
        let _: Vec<Event> = result.collect().await;

        // Wait for TTL to expire
        tokio::time::sleep(Duration::from_millis(200)).await;

        // Create enough new conversations to trigger eviction
        let new_events: Vec<Event> = (0..10)
            .map(|i| {
                make_causal_event(
                    2000 + i as u64,
                    &format!("conv_new_{:03}", i),
                    (200 + i) as u32,
                    "intent",
                )
            })
            .collect();

        // Process them to trigger the eviction check... 
        // Actually, TTL eviction happens when max_capacity is reached.
        // For this test, let's verify the mechanism differently.
        
        // The old conversation should have expired TTL but still be present
        // since we haven't hit max_capacity. Let's check it's still there.
        let state_old = analyzer.get_state(&ConversationId("conv_old".into()));
        assert!(state_old.is_some(), "Old conversation should still be present (capacity not reached)");
        
        // Force eviction by filling to capacity
        let fill_events: Vec<Event> = (0..100)
            .map(|i| {
                make_causal_event(
                    3000 + i as u64,
                    &format!("conv_fill_{:03}", i),
                    (300 + i) as u32,
                    "intent",
                )
            })
            .collect();

        let stream: EventStream = Box::pin(futures::stream::iter(fill_events));
        let result = analyzer.process(stream).await.unwrap();
        let _: Vec<Event> = result.collect().await;

        // Now the old conversation should have been evicted
        let state_old = analyzer.get_state(&ConversationId("conv_old".into()));
        assert!(
            state_old.is_none() || state_old.as_ref().map(|s| s.status) == Some(ConversationStatus::Expired),
            "Old conversation should be evicted after capacity reached"
        );
    }

    #[tokio::test]
    async fn test_avg_health_score() {
        let config = ConversationFlowConfig::default()
            .with_stall_timeout(Duration::from_secs(300));
        let mut analyzer = ConversationFlowAnalyzer::with_config(config);

        // Create two active conversations at the same timestamp
        let events = vec![
            make_causal_event(1000, "conv_a", 101, "intent"),
            make_causal_event(1000, "conv_b", 102, "intent"),
        ];

        let stream: EventStream = Box::pin(futures::stream::iter(events));
        let result = analyzer.process(stream).await.unwrap();
        let _: Vec<Event> = result.collect().await;

        // Both should have health_score 1.0 (just created)
        let avg = analyzer.avg_health_score();
        assert!((avg - 1.0).abs() < 0.01, "Avg health score should be ~1.0 right after creation, got {}", avg);
    }
}
