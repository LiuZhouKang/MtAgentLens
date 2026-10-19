// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// baseline.rs — Per-agent behavioral baseline tracking.
//
// Inspired by CrowdStrike Signal's self-learning per-entity baselines.
// Maintains statistical profiles (syscall frequency, file access patterns,
// network connection diversity) for each agent.  Deviation from baseline
// feeds the D6 Behavioral dimension.

use std::collections::HashMap;

/// Per-agent behavioral baseline profile.
#[derive(Debug, Clone)]
pub struct BehaviorBaseline {
    /// Syscall name → expected frequency (calls/sec).
    pub syscall_profile: HashMap<String, f64>,
    /// File path prefix → expected access frequency.
    pub file_access_dirs: HashMap<String, f64>,
    /// Network port → expected connection frequency.
    pub network_ports: HashMap<u16, f64>,
    /// Average tool call rate (calls/minute).
    pub tool_call_rate: f64,
    /// When the baseline was established (ns).
    pub established_at_ns: u64,
    /// Number of samples used to build the baseline.
    pub sample_count: u64,
}

impl Default for BehaviorBaseline {
    fn default() -> Self {
        Self {
            syscall_profile: HashMap::new(),
            file_access_dirs: HashMap::new(),
            network_ports: HashMap::new(),
            tool_call_rate: 0.0,
            established_at_ns: 0,
            sample_count: 0,
        }
    }
}

impl BehaviorBaseline {
    /// Whether enough samples have been collected for a reliable baseline.
    pub fn is_established(&self) -> bool {
        self.sample_count >= 50
    }

    /// Compute the deviation score for a single syscall event.
    ///
    /// Returns [0, 1] where 0 = perfectly normal, 1 = extreme anomaly.
    pub fn syscall_deviation(&self, syscall_name: &str, recent_rate: f64) -> f64 {
        let expected = self
            .syscall_profile
            .get(syscall_name)
            .copied()
            .unwrap_or(0.0);
        if expected < 1e-9 {
            // Never-before-seen syscall — moderate anomaly
            return 0.4;
        }
        let ratio = recent_rate / expected.max(1e-9);
        // 5× deviation → full 1.0
        (ratio / 5.0).min(1.0)
    }

    /// Compute deviation for a file access.
    ///
    /// Returns [0, 1] where 0 = normal directory, 1 = completely new.
    pub fn file_deviation(&self, path: &str) -> f64 {
        if self.file_access_dirs.is_empty() {
            return 0.0; // baseline not yet established
        }
        let matched = self
            .file_access_dirs
            .keys()
            .any(|prefix| path.starts_with(prefix));
        if matched {
            0.1 // known directory — low deviation
        } else {
            0.5 // new directory — moderate deviation
        }
    }

    /// Compute deviation for a network connection.
    pub fn network_deviation(&self, port: u16) -> f64 {
        if self.network_ports.is_empty() {
            return 0.0;
        }
        if self.network_ports.contains_key(&port) {
            0.1
        } else if is_known_suspicious_port(port) {
            0.9
        } else {
            0.4
        }
    }

    /// Aggregate deviation across all signal types.
    pub fn aggregate_deviation(
        &self,
        syscall_deviations: &[f64],
        file_deviations: &[f64],
        network_deviations: &[f64],
    ) -> f64 {
        let mut all = Vec::new();
        all.extend_from_slice(syscall_deviations);
        all.extend_from_slice(file_deviations);
        all.extend_from_slice(network_deviations);
        if all.is_empty() {
            return 0.0;
        }
        all.iter().sum::<f64>() / all.len() as f64
    }
}

/// Known suspicious ports (C2, exfiltration, tunneling).
fn is_known_suspicious_port(port: u16) -> bool {
    matches!(
        port,
        4444 | 1337 | 31337 | 6666 | 6667 | 8080 | 8443 | 9001 | 9999
    )
}

/// Tracker that builds and updates per-agent baselines online.
#[derive(Debug, Clone, Default)]
pub struct BaselineTracker {
    /// Per-agent baselines.
    baselines: HashMap<String, BehaviorBaseline>,
    /// Pending samples per agent (not yet incorporated into baseline).
    pending: HashMap<String, Vec<BehaviorSample>>,
}

/// A single behavior sample from an event.
#[derive(Debug, Clone)]
pub struct BehaviorSample {
    pub syscall_name: Option<String>,
    pub file_path: Option<String>,
    pub port: Option<u16>,
    pub is_tool_call: bool,
    pub timestamp_ns: u64,
}

impl BaselineTracker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed a behavior sample for an agent.
    pub fn observe(&mut self, agent_id: &str, sample: BehaviorSample) {
        self.pending
            .entry(agent_id.to_string())
            .or_default()
            .push(sample);
    }

    /// Rebuild the baseline for an agent from accumulated samples.
    pub fn rebuild_baseline(&mut self, agent_id: &str, now_ns: u64) {
        let samples = match self.pending.remove(agent_id) {
            Some(s) => s,
            None => return,
        };

        let mut baseline = self.baselines.remove(agent_id).unwrap_or_default();

        for s in &samples {
            if let Some(ref name) = s.syscall_name {
                *baseline.syscall_profile.entry(name.clone()).or_insert(0.0) += 1.0;
            }
            if let Some(ref path) = s.file_path {
                // Use first 2 path components as prefix
                let prefix = path.split('/').take(3).collect::<Vec<_>>().join("/");
                *baseline.file_access_dirs.entry(prefix).or_insert(0.0) += 1.0;
            }
            if let Some(port) = s.port {
                *baseline.network_ports.entry(port).or_insert(0.0) += 1.0;
            }
            if s.is_tool_call {
                baseline.tool_call_rate += 1.0;
            }
        }

        baseline.sample_count += samples.len() as u64;
        baseline.established_at_ns = now_ns;

        // Normalize frequencies
        let n = baseline.sample_count as f64;
        for v in baseline.syscall_profile.values_mut() {
            *v /= n;
        }
        for v in baseline.file_access_dirs.values_mut() {
            *v /= n;
        }
        for v in baseline.network_ports.values_mut() {
            *v /= n;
        }
        baseline.tool_call_rate /= n;

        self.baselines.insert(agent_id.to_string(), baseline);
    }

    /// Get the baseline for an agent, if established.
    pub fn get(&self, agent_id: &str) -> Option<&BehaviorBaseline> {
        self.baselines.get(agent_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_baseline_not_established_initially() {
        let baseline = BehaviorBaseline::default();
        assert!(!baseline.is_established());
    }

    #[test]
    fn test_syscall_deviation_normal() {
        let mut baseline = BehaviorBaseline::default();
        baseline.syscall_profile.insert("openat".into(), 0.5);
        let dev = baseline.syscall_deviation("openat", 0.5);
        assert!(
            dev < 0.3,
            "normal rate should have low deviation, got {dev}"
        );
    }

    #[test]
    fn test_syscall_deviation_anomaly() {
        let mut baseline = BehaviorBaseline::default();
        baseline.syscall_profile.insert("openat".into(), 0.1);
        let dev = baseline.syscall_deviation("openat", 1.0);
        assert!(
            dev > 0.5,
            "10× normal rate should have high deviation, got {dev}"
        );
    }

    #[test]
    fn test_file_deviation_new_path() {
        let mut baseline = BehaviorBaseline::default();
        baseline
            .file_access_dirs
            .insert("/home/user/project".into(), 0.3);
        baseline.sample_count = 50;
        let dev = baseline.file_deviation("/etc/shadow");
        assert!(dev > 0.3, "new path should trigger deviation, got {dev}");
    }

    #[test]
    fn test_tracker_builds_baseline() {
        let mut tracker = BaselineTracker::new();
        for _ in 0..60 {
            tracker.observe(
                "agent-1",
                BehaviorSample {
                    syscall_name: Some("openat".into()),
                    file_path: Some("/home/user/src/main.rs".into()),
                    port: None,
                    is_tool_call: false,
                    timestamp_ns: 1000,
                },
            );
        }
        tracker.rebuild_baseline("agent-1", 2000);
        let baseline = tracker.get("agent-1").unwrap();
        assert!(baseline.is_established());
    }
}
