// SPDX-License-Identifier: MIT
//
// progressive.rs — v9 Six-Dimension Progressive Threat Scorer.
//
// Upgraded from single-dimension EWMA/CUSUM (v8) to per-dimension
// independent tracking with per-agent state.  Each of the 6 scoring
// dimensions maintains its own EWMA, CUSUM accumulator, event buffer,
// and temporal decay weight.
//
// Dimensions:
//   D1: Boundary severity (PBC edges, taint depth, credential exposure)
//   D2: Content threat (regex → density → DeBERTa, injection patterns)
//   D3: Agentic amplification (tool diversity, delegation depth, shell access)
//   D4: Temporal dynamics (burst rate, duration, recency)
//   D5: Cross-context corroboration (independent evidence source convergence)
//   D6: Behavioral baseline deviation (per-agent syscall/file/network profiles)

use std::collections::HashMap;

use crate::responsibility::types::AgentId;

/// Six scoring dimensions.
pub const NUM_DIMS: usize = 6;

/// Dimension indices for readable code.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(usize)]
pub enum DimIndex {
    D1Boundary = 0,
    D2Content = 1,
    D3Agentic = 2,
    D4Temporal = 3,
    D5CrossContext = 4,
    D6Behavioral = 5,
}

impl DimIndex {
    pub const ALL: [DimIndex; NUM_DIMS] = [
        DimIndex::D1Boundary,
        DimIndex::D2Content,
        DimIndex::D3Agentic,
        DimIndex::D4Temporal,
        DimIndex::D5CrossContext,
        DimIndex::D6Behavioral,
    ];

    pub fn name(self) -> &'static str {
        match self {
            DimIndex::D1Boundary => "D1_boundary",
            DimIndex::D2Content => "D2_content",
            DimIndex::D3Agentic => "D3_agentic",
            DimIndex::D4Temporal => "D4_temporal",
            DimIndex::D5CrossContext => "D5_cross_context",
            DimIndex::D6Behavioral => "D6_behavioral",
        }
    }

    pub fn from_usize(i: usize) -> Self {
        match i {
            0 => DimIndex::D1Boundary,
            1 => DimIndex::D2Content,
            2 => DimIndex::D3Agentic,
            3 => DimIndex::D4Temporal,
            4 => DimIndex::D5CrossContext,
            _ => DimIndex::D6Behavioral,
        }
    }
}

/// Default dimension weights (per synthesis recommendation).
pub const DEFAULT_DIM_WEIGHTS: [f64; NUM_DIMS] = [0.28, 0.25, 0.20, 0.10, 0.10, 0.07];

/// CUSUM delta (allowable deviation) per dimension.
pub const DEFAULT_CUSUM_DELTA: [f64; NUM_DIMS] = [0.30, 0.40, 0.20, 0.30, 0.25, 0.35];

/// Per-dimension progressive tracking state.
#[derive(Debug, Clone)]
pub struct DimProgressiveState {
    /// Exponential weighted moving average.
    pub ewma: f64,
    /// Cumulative sum drift detector.
    pub cusum: f64,
    /// Consecutive periods with positive CUSUM (alarm after K).
    pub consecutive: u32,
    /// Timestamp (ns) of last update.
    pub last_update_ns: u64,
    /// Time-decayed score computed from buffered events.
    pub decayed_score: f64,
    /// Recent event scores with timestamps for decay computation.
    pub event_scores: Vec<(u64, f64)>,
}

impl Default for DimProgressiveState {
    fn default() -> Self {
        Self {
            ewma: 0.0,
            cusum: 0.0,
            consecutive: 0,
            last_update_ns: 0,
            decayed_score: 0.0,
            event_scores: Vec::new(),
        }
    }
}

/// Per-agent progressive scoring state.
#[derive(Debug, Clone)]
pub struct AgentScoreState {
    pub agent_id: AgentId,
    pub dims: [DimProgressiveState; NUM_DIMS],
    pub composite_raw: f64,
    pub composite_calibrated: f64,
    pub last_fusion_ns: u64,
    pub version: u64,
}

impl AgentScoreState {
    pub fn new(agent_id: AgentId, now_ns: u64) -> Self {
        Self {
            agent_id,
            dims: Default::default(),
            composite_raw: 0.0,
            composite_calibrated: 0.0,
            last_fusion_ns: now_ns,
            version: 0,
        }
    }

    pub fn any_cusum_alarm(&self, config: &ProgressiveConfig) -> bool {
        self.dims
            .iter()
            .any(|d| d.cusum > config.cusum_h && d.consecutive >= config.consecutive_k)
    }

    pub fn dim_scores(&self) -> [f64; NUM_DIMS] {
        [
            self.dims[0].decayed_score,
            self.dims[1].decayed_score,
            self.dims[2].decayed_score,
            self.dims[3].decayed_score,
            self.dims[4].decayed_score,
            self.dims[5].decayed_score,
        ]
    }
}

/// Configuration for the progressive scorer.
#[derive(Debug, Clone)]
pub struct ProgressiveConfig {
    pub ewma_alpha: f64,
    pub cusum_delta: [f64; NUM_DIMS],
    pub cusum_h: f64,
    pub consecutive_k: u32,
    pub half_life_ns: u64,
    pub max_event_window: usize,
    pub cutoff_ns: u64,
}

impl Default for ProgressiveConfig {
    fn default() -> Self {
        Self {
            ewma_alpha: 0.1,
            cusum_delta: DEFAULT_CUSUM_DELTA,
            cusum_h: 2.5,
            consecutive_k: 3,
            half_life_ns: 300_000_000_000,
            max_event_window: 200,
            cutoff_ns: 600_000_000_000,
        }
    }
}

/// Result of a progressive scoring update.
#[derive(Debug, Clone)]
pub struct ProgressiveResult {
    pub pts: f64,
    pub ewma: f64,
    pub cusum: f64,
    pub cusum_alarm: bool,
    pub event_count: u64,
    pub decay_weight: f64,
}

/// Progressive threat scorer with per-agent six-dimension tracking.
#[derive(Debug, Clone)]
pub struct ProgressiveScorer {
    pub config: ProgressiveConfig,
    pub agents: HashMap<AgentId, AgentScoreState>,
}

impl ProgressiveScorer {
    pub fn new(config: ProgressiveConfig) -> Self {
        Self {
            config,
            agents: HashMap::new(),
        }
    }

    pub fn get_or_create(&mut self, agent_id: &AgentId, now_ns: u64) -> &mut AgentScoreState {
        self.agents
            .entry(agent_id.clone())
            .or_insert_with(|| AgentScoreState::new(agent_id.clone(), now_ns))
    }

    /// Update a single dimension for an agent with a new raw score.
    pub fn update_dimension(
        &mut self,
        agent_id: &AgentId,
        dim: DimIndex,
        raw_score: f64,
        now_ns: u64,
    ) -> ProgressiveResult {
        // Clone config before borrowing self for agent state
        let config = self.config.clone();
        let state = self.get_or_create(agent_id, now_ns);
        let dim_state = &mut state.dims[dim as usize];

        prune_expired(&mut dim_state.event_scores, config.cutoff_ns, now_ns);
        dim_state.event_scores.push((now_ns, raw_score));
        if dim_state.event_scores.len() > config.max_event_window {
            dim_state.event_scores.remove(0);
        }

        let old_ewma = dim_state.ewma;
        dim_state.ewma = config.ewma_alpha * raw_score + (1.0 - config.ewma_alpha) * old_ewma;

        let delta = config.cusum_delta[dim as usize];
        let deviation = raw_score - (dim_state.ewma + delta);
        dim_state.cusum = (dim_state.cusum + deviation).max(0.0);
        if deviation > 0.0 {
            dim_state.consecutive += 1;
        } else {
            dim_state.consecutive = 0;
        }

        dim_state.decayed_score =
            compute_decay_sum(&dim_state.event_scores, config.half_life_ns, now_ns);
        dim_state.last_update_ns = now_ns;
        state.version += 1;

        ProgressiveResult {
            pts: dim_state.decayed_score,
            ewma: dim_state.ewma,
            cusum: dim_state.cusum,
            cusum_alarm: dim_state.cusum > config.cusum_h
                && dim_state.consecutive >= config.consecutive_k,
            event_count: dim_state.event_scores.len() as u64,
            decay_weight: temporal_decay_weight(now_ns, now_ns, config.half_life_ns),
        }
    }

    /// Get the fused (OWA+GeoMean) score for an agent from current state.
    ///
    /// Reads each dimension's EWMA and passes through `fuse_dimensions()`.
    /// Returns `None` if the agent has no state.
    pub fn get_agent_fused_score(&self, agent_id: &AgentId) -> Option<f64> {
        let state = self.agents.get(agent_id)?;
        let dims: [f64; NUM_DIMS] = [
            state.dims[0].ewma,
            state.dims[1].ewma,
            state.dims[2].ewma,
            state.dims[3].ewma,
            state.dims[4].ewma,
            state.dims[5].ewma,
        ];
        // Use default weights matching the scoring system
        let weights: [f64; NUM_DIMS] =
            crate::responsibility::scoring::progressive::DEFAULT_DIM_WEIGHTS;
        Some(crate::responsibility::scoring::fusion::fuse_dimensions(
            &dims, &weights,
        ))
    }

    /// Check if any dimension for an agent has a CUSUM alarm.
    ///
    /// Returns `(alarm_count, alarmed_dimensions)` where alarmed_dimensions
    /// lists the dimension indices with active alarms.
    pub fn cusum_alarms(&self, agent_id: &AgentId) -> (usize, Vec<usize>) {
        let state = match self.agents.get(agent_id) {
            Some(s) => s,
            None => return (0, vec![]),
        };
        let config = &self.config;
        let alarmed: Vec<usize> = (0..NUM_DIMS)
            .filter(|&i| {
                state.dims[i].cusum > config.cusum_h
                    && state.dims[i].consecutive >= config.consecutive_k
            })
            .collect();
        (alarmed.len(), alarmed)
    }

    /// Check for evidence conflict: D1 (Boundary) high but D5 (CrossContext) low.
    ///
    /// When boundary severity is elevated without corroboration, this may
    /// indicate a false positive or an early-stage attack.
    pub fn evidence_conflict(&self, agent_id: &AgentId) -> bool {
        let state = match self.agents.get(agent_id) {
            Some(s) => s,
            None => return false,
        };
        let d1 = state.dims[DimIndex::D1Boundary as usize].ewma;
        let d5 = state.dims[DimIndex::D5CrossContext as usize].ewma;
        d1 > 0.5 && d5 < 0.2
    }
}

// ── Temporal helpers ──────────────────────────────────────────────────────

pub fn temporal_decay_weight(timestamp_ns: u64, now_ns: u64, half_life_ns: u64) -> f64 {
    let age_ns = now_ns.saturating_sub(timestamp_ns);
    if age_ns == 0 {
        return 1.0;
    }
    let lambda = (2.0_f64).ln() / (half_life_ns as f64);
    (-lambda * (age_ns as f64)).exp()
}

pub fn compute_decay_sum(events: &[(u64, f64)], half_life_ns: u64, now_ns: u64) -> f64 {
    if events.is_empty() {
        return 0.0;
    }
    let total_weight: f64 = events
        .iter()
        .map(|(ts, _)| temporal_decay_weight(*ts, now_ns, half_life_ns))
        .sum();
    if total_weight < 1e-9 {
        return 0.0;
    }
    let weighted_sum: f64 = events
        .iter()
        .map(|(ts, score)| temporal_decay_weight(*ts, now_ns, half_life_ns) * score)
        .sum();
    weighted_sum / total_weight
}

fn prune_expired(events: &mut Vec<(u64, f64)>, cutoff_ns: u64, now_ns: u64) {
    let threshold = now_ns.saturating_sub(cutoff_ns);
    events.retain(|(ts, _)| *ts >= threshold);
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn aid() -> AgentId {
        AgentId::from("test-agent")
    }

    #[test]
    fn test_progressive_defaults() {
        let mut scorer = ProgressiveScorer::new(ProgressiveConfig::default());
        let result = scorer.update_dimension(&aid(), DimIndex::D1Boundary, 0.5, 1000);
        assert!(result.pts >= 0.0);
        assert!(result.ewma >= 0.0);
        assert!(result.cusum >= 0.0);
    }

    #[test]
    fn test_ewma_converges() {
        let mut scorer = ProgressiveScorer::new(ProgressiveConfig {
            ewma_alpha: 0.5,
            ..Default::default()
        });
        for i in 0..20 {
            scorer.update_dimension(&aid(), DimIndex::D1Boundary, 0.8, i * 1000);
        }
        let state = scorer.agents.get(&aid()).unwrap();
        assert!((state.dims[0].ewma - 0.8).abs() < 0.01);
    }

    #[test]
    fn test_cusum_detects_sustained_shift() {
        let mut scorer = ProgressiveScorer::new(ProgressiveConfig {
            ewma_alpha: 0.3,
            cusum_h: 0.5,
            consecutive_k: 2,
            cusum_delta: [0.05, 0.05, 0.05, 0.05, 0.05, 0.05],
            ..Default::default()
        });
        for i in 0..10 {
            scorer.update_dimension(&aid(), DimIndex::D1Boundary, 0.05, i * 1000);
        }
        let mut alarmed = false;
        for i in 10..30 {
            let result = scorer.update_dimension(&aid(), DimIndex::D1Boundary, 0.9, i * 1000);
            if result.cusum_alarm {
                alarmed = true;
                break;
            }
        }
        assert!(alarmed, "CUSUM should detect sustained upward shift");
    }

    #[test]
    fn test_temporal_decay() {
        let w = temporal_decay_weight(0, 300_000_000_000, 300_000_000_000);
        assert!((w - 0.5).abs() < 0.01, "half-life should give weight 0.5");
    }

    #[test]
    fn test_dim_independent_tracking() {
        let mut scorer = ProgressiveScorer::new(ProgressiveConfig::default());
        scorer.update_dimension(&aid(), DimIndex::D1Boundary, 0.9, 1000);
        scorer.update_dimension(&aid(), DimIndex::D2Content, 0.1, 1000);
        let state = scorer.agents.get(&aid()).unwrap();
        assert!(state.dims[0].decayed_score > 0.5, "D1 should be high");
        assert!(state.dims[1].decayed_score < 0.3, "D2 should be low");
    }
}
