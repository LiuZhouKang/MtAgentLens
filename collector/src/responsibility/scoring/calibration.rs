// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// calibration.rs — Multi-feature Platt calibration with ECE monitoring.
//
// Implements Yousef et al. (IEEE TDSC 2023) multi-source calibration:
// treats K detector raw scores as features into logistic regression,
// producing P(attack | all_signals).  Monitors Expected Calibration Error
// (Naeini et al. AAAI 2015) and triggers re-calibration when ECE > 0.05.

use std::collections::VecDeque;

use crate::responsibility::scoring::fusion::platt_multi;
use crate::responsibility::scoring::progressive::NUM_DIMS;

/// Multi-feature Platt calibrator.
///
/// Maintains per-dimension weights plus bias term for logistic regression:
/// P(attack | d1..d6) = 1 / (1 + exp(-(w0 + w1*d1 + ... + w6*d6)))
#[derive(Debug, Clone)]
pub struct MultiFeatureCalibrator {
    /// w0 (bias) + w1..w6 (per-dimension weights)
    pub weights: [f64; NUM_DIMS + 1],
    /// Whether the calibrator has been trained on labeled data.
    pub trained: bool,
}

impl Default for MultiFeatureCalibrator {
    fn default() -> Self {
        Self {
            // Default: reasonable starting weights (bias negative = skeptical)
            weights: [-3.0, 7.0, 5.0, 3.0, 1.0, 2.0, 1.5],
            trained: false,
        }
    }
}

impl MultiFeatureCalibrator {
    pub fn new() -> Self {
        Self::default()
    }

    /// Predict calibrated probability from dimension scores.
    pub fn predict(&self, dim_scores: &[f64; NUM_DIMS]) -> f64 {
        platt_multi(dim_scores, &self.weights)
    }

    /// Online SGD update with a single labeled example.
    ///
    /// lr: learning rate.  Small values (~0.01) for stable online updates.
    pub fn online_update(&mut self, dim_scores: &[f64; NUM_DIMS], label: bool, lr: f64) {
        let p = self.predict(dim_scores);
        let y = if label { 1.0 } else { 0.0 };
        let error = y - p;

        // Gradient of logistic loss: (y - p) * x_i
        self.weights[0] += lr * error; // bias update
        for i in 0..NUM_DIMS {
            self.weights[i + 1] += lr * error * dim_scores[i];
        }
        self.trained = true;
    }

    /// Batch fit using gradient descent on labeled dataset.
    pub fn batch_fit(&mut self, dataset: &[([f64; NUM_DIMS], bool)], epochs: usize, lr: f64) {
        if dataset.is_empty() {
            return;
        }
        for _ in 0..epochs {
            for (dims, label) in dataset {
                self.online_update(dims, *label, lr);
            }
        }
        self.trained = true;
    }
}

/// Calibration monitor tracking Expected Calibration Error.
#[derive(Debug, Clone)]
pub struct CalibrationMonitor {
    /// Recent (calibrated_prob, actual_label) pairs.
    predictions: VecDeque<(f64, bool)>,
    /// Maximum window size.
    max_window: usize,
    /// Number of bins for ECE computation.
    n_bins: usize,
    /// ECE threshold for re-calibration trigger.
    ece_threshold: f64,
    /// Multi-feature calibrator for online SGD updates.
    calibrator: MultiFeatureCalibrator,
}

impl Default for CalibrationMonitor {
    fn default() -> Self {
        Self {
            predictions: VecDeque::new(),
            max_window: 1000,
            n_bins: 15,
            ece_threshold: 0.05,
            calibrator: MultiFeatureCalibrator::new(),
        }
    }
}

impl CalibrationMonitor {
    pub fn new(max_window: usize, n_bins: usize, ece_threshold: f64) -> Self {
        Self {
            predictions: VecDeque::new(),
            max_window,
            n_bins,
            ece_threshold,
            calibrator: MultiFeatureCalibrator::new(),
        }
    }

    /// Access the calibrator for prediction.
    pub fn calibrator(&self) -> &MultiFeatureCalibrator {
        &self.calibrator
    }

    /// Feed the most recent prediction's dimension scores into the calibrator
    /// for online SGD update. The pseudo-label comes from the last record() call.
    /// Call this AFTER record() so the label is available.
    pub fn feed_calibrator(&mut self, dim_scores: &[f64; 6]) {
        if let Some((_prob, label)) = self.predictions.back() {
            self.calibrator.online_update(dim_scores, *label, 0.01);
        }
    }

    /// Record a prediction with its eventual ground-truth label.
    pub fn record(&mut self, calibrated_prob: f64, actual_label: bool) {
        self.predictions.push_back((calibrated_prob, actual_label));
        if self.predictions.len() > self.max_window {
            self.predictions.pop_front();
        }
    }

    /// Compute Expected Calibration Error (ECE).
    ///
    /// ECE = Σ (|B_m|/n) × |acc(B_m) - conf(B_m)|
    pub fn ece(&self) -> f64 {
        if self.predictions.is_empty() || self.n_bins == 0 {
            return 0.0;
        }
        let mut bins: Vec<Vec<(f64, bool)>> = vec![Vec::new(); self.n_bins];
        for (conf, label) in &self.predictions {
            let bin_idx = ((conf * self.n_bins as f64) as usize).min(self.n_bins - 1);
            bins[bin_idx].push((*conf, *label));
        }
        let n = self.predictions.len() as f64;
        let mut ece = 0.0;
        for bin in &bins {
            if bin.is_empty() {
                continue;
            }
            let avg_conf: f64 = bin.iter().map(|(c, _)| c).sum::<f64>() / bin.len() as f64;
            let acc: f64 = bin.iter().filter(|(_, l)| *l).count() as f64 / bin.len() as f64;
            ece += (bin.len() as f64 / n) * (acc - avg_conf).abs();
        }
        ece
    }

    /// Whether re-calibration should be triggered.
    pub fn needs_recalibration(&self) -> bool {
        self.predictions.len() >= 50 && self.ece() > self.ece_threshold
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn zeros() -> [f64; NUM_DIMS] {
        [0.0; NUM_DIMS]
    }
    fn ones() -> [f64; NUM_DIMS] {
        [0.9; NUM_DIMS]
    }

    #[test]
    fn test_calibrator_default_predict_in_range() {
        let cal = MultiFeatureCalibrator::new();
        let p = cal.predict(&zeros());
        assert!(p >= 0.0 && p <= 1.0);
    }

    #[test]
    fn test_calibrator_scores_monotonic() {
        let cal = MultiFeatureCalibrator::new();
        let p_low = cal.predict(&zeros());
        let p_high = cal.predict(&ones());
        assert!(p_low < p_high, "higher dims should give higher probability");
    }

    #[test]
    fn test_online_update_converges() {
        let mut cal = MultiFeatureCalibrator::new();
        // Train with positive examples: high dims → attack
        for _ in 0..100 {
            cal.online_update(&ones(), true, 0.01);
        }
        let p = cal.predict(&ones());
        assert!(
            p > 0.8,
            "after training, high dims should give high probability, got {p}"
        );
    }

    #[test]
    fn test_ece_perfect_calibration() {
        let mut mon = CalibrationMonitor::default();
        // Perfect calibration: 0.8 prob → 80% accuracy
        for _ in 0..80 {
            mon.record(0.8, true);
        }
        for _ in 0..20 {
            mon.record(0.8, false);
        }
        let ece = mon.ece();
        assert!(
            ece < 0.05,
            "perfect calibration should have low ECE, got {ece}"
        );
    }

    #[test]
    fn test_ece_detects_miscalibration() {
        let mut mon = CalibrationMonitor::default();
        // Miscalibrated: 0.9 prob but only 10% accurate
        for _ in 0..10 {
            mon.record(0.9, true);
        }
        for _ in 0..90 {
            mon.record(0.9, false);
        }
        let ece = mon.ece();
        assert!(ece > 0.3, "miscalibrated should have high ECE, got {ece}");
    }
}
