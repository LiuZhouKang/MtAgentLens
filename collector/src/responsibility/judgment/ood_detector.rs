// SPDX-License-Identifier: MIT
//
// ood_detector.rs — Layer 4 Online OOD (Out-of-Distribution) detection.
//
// STATUS (2026-06-16): Implemented but NOT integrated into the main runtime
// scoring pipeline. Currently only used by sse_content_scanner.rs (signals
// module), which is not connected to runtime scoring.
//
// DESIGN: Per-tool n-gram language model. Calibration phase (120s + 200
// samples) builds unigram/bigram frequency models. After calibration, new
// text is scored by unseen-ngram ratio — high ratio = out-of-distribution.
//
// LIMITATION FOR BENCHMARK: Requires 120s+ agent sessions to activate.
// Our 86-scenario benchmark has 30-90s sessions — OOD never fires.
// Suitable for long-running production agent monitoring.
//
// FUTURE: Integrate into runtime scoring as a D2 (Content) dimension
// enhancer for sessions exceeding calibration threshold.

use std::collections::HashMap;

/// Calibration duration: 2 minutes (in nanoseconds).
const CALIBRATION_DURATION_NS: u64 = 120_000_000_000;
/// Minimum samples before detection activates.
const MIN_CALIBRATION_SAMPLES: usize = 200;
/// Anomaly score threshold: values > this are flagged as OOD.
pub const ANOMALY_THRESHOLD: f32 = 0.95;

pub struct OnlineOodDetector {
    session_start_ns: u64,
    per_tool_models: HashMap<String, NgramModel>,
    calibration_samples: usize,
}

impl OnlineOodDetector {
    pub fn new(session_start_ns: u64) -> Self {
        Self {
            session_start_ns,
            per_tool_models: HashMap::new(),
            calibration_samples: 0,
        }
    }

    /// Update the session start time (called after GraphBuilder construction
    /// when the actual session timestamp is known).
    pub fn set_session_start(&mut self, ts_ns: u64) {
        self.session_start_ns = ts_ns;
    }

    fn calibrate(&mut self, text: &str, tool_name: &str) {
        let model = self
            .per_tool_models
            .entry(tool_name.to_string())
            .or_insert_with(NgramModel::new);
        model.add_document(text);
        self.calibration_samples += 1;
    }

    /// Returns None during calibration, Some(score) after calibration.
    pub fn detect(&mut self, text: &str, tool_name: &str, ts_ns: u64) -> Option<f32> {
        let elapsed = ts_ns.saturating_sub(self.session_start_ns);

        if elapsed < CALIBRATION_DURATION_NS || self.calibration_samples < MIN_CALIBRATION_SAMPLES {
            self.calibrate(text, tool_name);
            return None;
        }

        let model = match self.per_tool_models.get(tool_name) {
            Some(m) => m,
            None => {
                self.calibrate(text, tool_name);
                return None;
            }
        };

        Some(model.anomaly_score(text))
    }
}

struct NgramModel {
    unigram_freq: HashMap<String, f32>,
    bigram_freq: HashMap<String, f32>,
    total_tokens: usize,
    total_docs: usize,
}

impl NgramModel {
    fn new() -> Self {
        Self {
            unigram_freq: HashMap::new(),
            bigram_freq: HashMap::new(),
            total_tokens: 0,
            total_docs: 0,
        }
    }

    fn add_document(&mut self, text: &str) {
        let tokens: Vec<String> = text.split_whitespace().map(|s| s.to_lowercase()).collect();
        self.total_docs += 1;
        self.total_tokens += tokens.len();

        for t in &tokens {
            *self.unigram_freq.entry(t.clone()).or_insert(0.0) += 1.0;
        }

        for pair in tokens.windows(2) {
            let key = format!("{} {}", pair[0], pair[1]);
            *self.bigram_freq.entry(key).or_insert(0.0) += 1.0;
        }
    }

    fn anomaly_score(&self, text: &str) -> f32 {
        let tokens: Vec<String> = text.split_whitespace().map(|s| s.to_lowercase()).collect();
        if tokens.is_empty() {
            return 0.0;
        }

        let total = tokens.len() as f32;
        let mut unseen_unigrams = 0u32;
        let mut unseen_bigrams = 0u32;
        let bigram_count = tokens.len().saturating_sub(1);

        for t in &tokens {
            if !self.unigram_freq.contains_key(t) {
                unseen_unigrams += 1;
            }
        }

        for pair in tokens.windows(2) {
            let key = format!("{} {}", pair[0], pair[1]);
            if !self.bigram_freq.contains_key(&key) {
                unseen_bigrams += 1;
            }
        }

        let unigram_score = unseen_unigrams as f32 / total.max(1.0);
        let bigram_score = if bigram_count > 0 {
            unseen_bigrams as f32 / bigram_count as f32
        } else {
            0.0
        };

        (unigram_score * 0.4 + bigram_score * 0.6).min(1.0)
    }
}
