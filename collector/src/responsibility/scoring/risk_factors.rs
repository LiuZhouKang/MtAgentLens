// SPDX-License-Identifier: MIT
//
// risk_factors.rs — Splunk RBA-style entity risk factor aggregation.
//
// Implements Total = (base + Σ additive) × Π multiplicative
// with configurable risk factors per agent and per event type.

use serde::{Deserialize, Serialize};

/// A single risk factor value.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskFactor {
    /// Human-readable factor name.
    pub name: String,
    /// Numeric contribution.
    pub value: f64,
}

/// Complete set of risk factors for an alert.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RiskFactors {
    /// Additive risk factors (summed).
    pub additive: Vec<RiskFactor>,
    /// Multiplicative risk factors (product).
    pub multiplicative: Vec<RiskFactor>,
}

impl RiskFactors {
    pub fn new() -> Self {
        Self::default()
    }

    /// Add an additive factor.
    pub fn add(&mut self, name: &str, value: f64) {
        self.additive.push(RiskFactor {
            name: name.to_string(),
            value,
        });
    }

    /// Add a multiplicative factor.
    pub fn multiply(&mut self, name: &str, value: f64) {
        self.multiplicative.push(RiskFactor {
            name: name.to_string(),
            value,
        });
    }
}

/// Compute the total risk score using the Splunk RBA formula.
///
/// Total = (base + Σ additive) × Π multiplicative
pub fn compute_risk_score(base: f64, factors: &RiskFactors) -> f64 {
    let additive: f64 = factors.additive.iter().map(|f| f.value).sum();
    let multiplicative: f64 = factors
        .multiplicative
        .iter()
        .map(|f| f.value)
        .product();
    (base + additive) * multiplicative.max(1.0)
}

/// Standard risk factors for agent security scoring.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskFactorConfig {
    /// Additive factors and their default values.
    pub additive_defaults: Vec<RiskFactor>,
    /// Multiplicative factors and their default values.
    pub multiplicative_defaults: Vec<RiskFactor>,
}

impl Default for RiskFactorConfig {
    fn default() -> Self {
        Self {
            additive_defaults: vec![
                RiskFactor { name: "admin_process".into(), value: 15.0 },
                RiskFactor { name: "repeated_alert".into(), value: 10.0 },
                RiskFactor { name: "high_sensitivity_target".into(), value: 20.0 },
                RiskFactor { name: "multi_agent_coordination".into(), value: 10.0 },
            ],
            multiplicative_defaults: vec![
                RiskFactor { name: "critical_destination".into(), value: 1.5 },
                RiskFactor { name: "privileged_context".into(), value: 2.0 },
            ],
        }
    }
}

impl RiskFactorConfig {
    /// Build risk factors from event context.
    ///
    /// Agent-level factors (admin, multi-agent) are always included.
    /// Event-level factors (critical dest, privileged) are conditional.
    pub fn compute_factors(
        &self,
        is_admin_process: bool,
        is_repeated: bool,
        is_critical_target: bool,
        is_multi_agent: bool,
        is_privileged_context: bool,
    ) -> RiskFactors {
        let mut factors = RiskFactors::new();

        if is_admin_process {
            factors.add("admin_process", 15.0);
        }
        if is_repeated {
            factors.add("repeated_alert", 10.0);
        }
        if is_critical_target {
            factors.add("high_sensitivity_target", 20.0);
        }
        if is_multi_agent {
            factors.add("multi_agent_coordination", 10.0);
        }
        if is_critical_target {
            factors.multiply("critical_destination", 1.5);
        }
        if is_privileged_context {
            factors.multiply("privileged_context", 2.0);
        }

        factors
    }
}

/// Alert severity based on risk score thresholds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RiskLevel {
    Info,
    Low,
    Medium,
    High,
    Critical,
}

impl RiskLevel {
    /// Map a risk score to severity level.
    pub fn from_score(score: f64) -> Self {
        if score >= 200.0 {
            RiskLevel::Critical
        } else if score >= 100.0 {
            RiskLevel::High
        } else if score >= 50.0 {
            RiskLevel::Medium
        } else if score >= 20.0 {
            RiskLevel::Low
        } else {
            RiskLevel::Info
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_risk_score_formula() {
        let mut factors = RiskFactors::new();
        factors.add("test_add", 10.0);
        factors.multiply("test_mul", 2.0);
        let score = compute_risk_score(50.0, &factors);
        assert!((score - 120.0).abs() < 0.01, "expected 120, got {score}");
    }

    #[test]
    fn test_risk_score_no_factors() {
        let factors = RiskFactors::new();
        let score = compute_risk_score(50.0, &factors);
        assert!((score - 50.0).abs() < 0.01);
    }

    #[test]
    fn test_risk_level_thresholds() {
        assert_eq!(RiskLevel::from_score(0.0), RiskLevel::Info);
        assert_eq!(RiskLevel::from_score(30.0), RiskLevel::Low);
        assert_eq!(RiskLevel::from_score(75.0), RiskLevel::Medium);
        assert_eq!(RiskLevel::from_score(150.0), RiskLevel::High);
        assert_eq!(RiskLevel::from_score(250.0), RiskLevel::Critical);
    }

    #[test]
    fn test_default_config_non_empty() {
        let config = RiskFactorConfig::default();
        assert!(!config.additive_defaults.is_empty());
        assert!(!config.multiplicative_defaults.is_empty());
    }
}
