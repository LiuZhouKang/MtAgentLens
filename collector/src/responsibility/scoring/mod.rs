// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// scoring/mod.rs — v9 unified scoring module entry point.
//
// Six-dimension progressive threat scoring with per-agent EWMA/CUSUM
// tracking, OWA+GeoMean fusion, multi-feature Platt calibration,
// behavioral baselines, Splunk RBA risk factors, and AIVSS computation.

pub mod baseline;
pub mod calibration;
pub mod fusion;
pub mod progressive;
pub mod risk_factors;

pub use baseline::{BaselineTracker, BehaviorBaseline, BehaviorSample};
pub use calibration::{CalibrationMonitor, MultiFeatureCalibrator};
pub use fusion::{
    compute_ece, compute_owa, compute_weighted_geometric_mean, fuse_dimensions, platt_multi,
    platt_sigmoid, platt_sigmoid_params,
};
pub use progressive::{
    AgentScoreState, DEFAULT_CUSUM_DELTA, DEFAULT_DIM_WEIGHTS, DimIndex, DimProgressiveState,
    NUM_DIMS, ProgressiveConfig, ProgressiveResult, ProgressiveScorer, compute_decay_sum,
    temporal_decay_weight,
};
pub use risk_factors::{RiskFactor, RiskFactorConfig, RiskFactors, RiskLevel, compute_risk_score};
