// SPDX-License-Identifier: MIT
//
// scoring/mod.rs — v9 unified scoring module entry point.
//
// Six-dimension progressive threat scoring with per-agent EWMA/CUSUM
// tracking, OWA+GeoMean fusion, multi-feature Platt calibration,
// behavioral baselines, Splunk RBA risk factors, and AIVSS computation.

pub mod progressive;
pub mod fusion;
pub mod calibration;
pub mod baseline;
pub mod risk_factors;

pub use progressive::{
    AgentScoreState, DimIndex, DimProgressiveState, ProgressiveConfig,
    ProgressiveResult, ProgressiveScorer, NUM_DIMS, DEFAULT_CUSUM_DELTA, DEFAULT_DIM_WEIGHTS,
    compute_decay_sum, temporal_decay_weight,
};
pub use fusion::{
    compute_ece, compute_owa, compute_weighted_geometric_mean, fuse_dimensions,
    platt_multi, platt_sigmoid, platt_sigmoid_params,
};
pub use calibration::{CalibrationMonitor, MultiFeatureCalibrator};
pub use baseline::{BaselineTracker, BehaviorBaseline, BehaviorSample};
pub use risk_factors::{
    RiskFactor, RiskFactorConfig, RiskFactors, RiskLevel, compute_risk_score,
};
