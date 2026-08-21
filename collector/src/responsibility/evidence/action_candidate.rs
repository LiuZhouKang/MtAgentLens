// SPDX-License-Identifier: MIT

use crate::responsibility::types::support_types::{ActionEvent, ProcessId};
use serde::{Deserialize, Serialize};

pub const ACTION_CANDIDATE_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ActionCandidateType {
    ProcessExec,
    FileRead,
    FileWrite,
    FileOverwrite,
    NetworkConnect,
    ProcessSpawn,
}

impl std::fmt::Display for ActionCandidateType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::ProcessExec => "process_exec",
            Self::FileRead => "file_read",
            Self::FileWrite => "file_write",
            Self::FileOverwrite => "file_overwrite",
            Self::NetworkConnect => "network_connect",
            Self::ProcessSpawn => "process_spawn",
        };
        write!(f, "{}", value)
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CandidateDerivationMode {
    Direct,
    Aggregated,
    MultiEmit,
}

impl std::fmt::Display for CandidateDerivationMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::Direct => "direct",
            Self::Aggregated => "aggregated",
            Self::MultiEmit => "multi_emit",
        };
        write!(f, "{}", value)
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Lossiness {
    Lossless,
    Lossy,
}

impl std::fmt::Display for Lossiness {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::Lossless => "lossless",
            Self::Lossy => "lossy",
        };
        write!(f, "{}", value)
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SignalClass {
    HighSignal,
    LowSignal,
    InternalHousekeeping,
}

impl std::fmt::Display for SignalClass {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::HighSignal => "high_signal",
            Self::LowSignal => "low_signal",
            Self::InternalHousekeeping => "internal_housekeeping",
        };
        write!(f, "{}", value)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ActionCandidate {
    pub schema_version: u32,
    pub candidate_id: String,
    pub timestamp_ns: u64,
    pub candidate_type: ActionCandidateType,
    pub process_id: ProcessId,
    pub normalized_args: serde_json::Value,
    pub raw_event_refs: Vec<String>,
    pub derivation_mode: CandidateDerivationMode,
    pub lossiness: Lossiness,
    pub agent_id: Option<String>,
    pub aggregate_window_ns: Option<u64>,
    pub aggregate_rule_name: Option<String>,
    pub signal_class: Option<SignalClass>,
    pub debug_notes: Option<String>,
}

impl ActionCandidate {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        candidate_id: String,
        timestamp_ns: u64,
        candidate_type: ActionCandidateType,
        process_id: ProcessId,
        normalized_args: serde_json::Value,
        raw_event_refs: Vec<String>,
        derivation_mode: CandidateDerivationMode,
        lossiness: Lossiness,
        agent_id: Option<String>,
        aggregate_window_ns: Option<u64>,
        aggregate_rule_name: Option<String>,
        signal_class: Option<SignalClass>,
        debug_notes: Option<String>,
    ) -> Result<Self, String> {
        let candidate = Self {
            schema_version: ACTION_CANDIDATE_SCHEMA_VERSION,
            candidate_id,
            timestamp_ns,
            candidate_type,
            process_id,
            normalized_args,
            raw_event_refs,
            derivation_mode,
            lossiness,
            agent_id,
            aggregate_window_ns,
            aggregate_rule_name,
            signal_class,
            debug_notes,
        };
        candidate.validate()?;
        Ok(candidate)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.raw_event_refs.is_empty() {
            return Err("raw_event_refs must not be empty".to_string());
        }

        match self.derivation_mode {
            CandidateDerivationMode::Direct => {
                if self.aggregate_window_ns.is_some() || self.aggregate_rule_name.is_some() {
                    return Err("direct candidates must not set aggregate fields".to_string());
                }
                if self.raw_event_refs.len() != 1 {
                    return Err(
                        "direct candidates must reference exactly one raw event".to_string()
                    );
                }
            }
            CandidateDerivationMode::Aggregated | CandidateDerivationMode::MultiEmit => {
                if self.aggregate_window_ns.is_none() || self.aggregate_rule_name.is_none() {
                    return Err(
                        "aggregated and multi_emit candidates must set aggregate_window_ns and aggregate_rule_name"
                            .to_string(),
                    );
                }
            }
        }

        Ok(())
    }

    pub fn from_action_event(action: &ActionEvent) -> Option<Self> {
        let (candidate_type, normalized_args, lossiness, signal_class) = classify_action(action)?;
        Self::new(
            uuid::Uuid::new_v4().to_string(),
            action.timestamp_ns,
            candidate_type,
            action.process_id,
            normalized_args,
            vec![action.action_id.clone()],
            CandidateDerivationMode::Direct,
            lossiness,
            action.agent_id.clone(),
            None,
            None,
            signal_class,
            None,
        )
        .ok()
    }
}

fn classify_action(
    action: &ActionEvent,
) -> Option<(
    ActionCandidateType,
    serde_json::Value,
    Lossiness,
    Option<SignalClass>,
)> {
    match action.syscall.as_str() {
        "execve" | "execveat" => Some((
            ActionCandidateType::ProcessExec,
            serde_json::json!({
                "argv": action.args.get("argv").cloned().unwrap_or(serde_json::Value::Null),
                "path": action.args.get("path").cloned().unwrap_or(serde_json::Value::Null),
            }),
            Lossiness::Lossy,
            Some(SignalClass::HighSignal),
        )),
        "clone" | "clone3" | "fork" | "vfork" => Some((
            ActionCandidateType::ProcessSpawn,
            serde_json::json!({
                "child_pid": action.args.get("child_pid").cloned().unwrap_or(serde_json::Value::Null),
                "flags": action.args.get("flags").cloned().unwrap_or(serde_json::Value::Null),
            }),
            Lossiness::Lossy,
            Some(SignalClass::InternalHousekeeping),
        )),
        "connect" => {
            let host = action.args.get("host").and_then(|v| v.as_str())?;
            Some((
                ActionCandidateType::NetworkConnect,
                serde_json::json!({
                    "host": host,
                    "port": action.args.get("port").cloned().unwrap_or(serde_json::Value::Null),
                }),
                Lossiness::Lossless,
                Some(SignalClass::HighSignal),
            ))
        }
        "open" | "openat" => {
            let path = action.args.get("path").and_then(|v| v.as_str())?;
            let flags = action
                .args
                .get("flags")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let candidate_type = if flags.contains("O_TRUNC") {
                ActionCandidateType::FileOverwrite
            } else if flags.contains("O_WRONLY")
                || flags.contains("O_RDWR")
                || flags.contains("O_CREAT")
            {
                ActionCandidateType::FileWrite
            } else {
                ActionCandidateType::FileRead
            };
            Some((
                candidate_type,
                serde_json::json!({
                    "path": path,
                    "flags": flags,
                }),
                Lossiness::Lossless,
                Some(SignalClass::HighSignal),
            ))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::support_types::ActionEvent;

    fn make_action(syscall: &str, args: serde_json::Value) -> ActionEvent {
        ActionEvent::new(
            "action-1".to_string(),
            ProcessId::new(42, 100),
            None,
            1_000,
            syscall.to_string(),
            args,
            "bash".to_string(),
        )
    }

    #[test]
    fn test_candidate_requires_raw_event_refs() {
        let err = ActionCandidate::new(
            "candidate-1".to_string(),
            1_000,
            ActionCandidateType::FileRead,
            ProcessId::new(1, 2),
            serde_json::json!({"path": "/tmp/x"}),
            vec![],
            CandidateDerivationMode::Direct,
            Lossiness::Lossless,
            None,
            None,
            None,
            Some(SignalClass::HighSignal),
            None,
        )
        .unwrap_err();

        assert!(err.contains("raw_event_refs"));
    }

    #[test]
    fn test_direct_candidate_rejects_aggregate_fields() {
        let err = ActionCandidate::new(
            "candidate-1".to_string(),
            1_000,
            ActionCandidateType::FileRead,
            ProcessId::new(1, 2),
            serde_json::json!({"path": "/tmp/x"}),
            vec!["action-1".to_string()],
            CandidateDerivationMode::Direct,
            Lossiness::Lossless,
            None,
            Some(500),
            Some("windowed".to_string()),
            Some(SignalClass::HighSignal),
            None,
        )
        .unwrap_err();

        assert!(err.contains("direct candidates"));
    }

    #[test]
    fn test_direct_candidate_requires_single_raw_event_ref() {
        let err = ActionCandidate::new(
            "candidate-1".to_string(),
            1_000,
            ActionCandidateType::FileRead,
            ProcessId::new(1, 2),
            serde_json::json!({"path": "/tmp/x"}),
            vec!["action-1".to_string(), "action-2".to_string()],
            CandidateDerivationMode::Direct,
            Lossiness::Lossless,
            None,
            None,
            None,
            Some(SignalClass::HighSignal),
            None,
        )
        .unwrap_err();

        assert!(err.contains("exactly one raw event"));
    }

    #[test]
    fn test_aggregated_candidate_requires_aggregate_fields() {
        let err = ActionCandidate::new(
            "candidate-1".to_string(),
            1_000,
            ActionCandidateType::FileRead,
            ProcessId::new(1, 2),
            serde_json::json!({"path": "/tmp/x"}),
            vec!["action-1".to_string()],
            CandidateDerivationMode::Aggregated,
            Lossiness::Lossy,
            None,
            Some(500),
            None,
            Some(SignalClass::LowSignal),
            None,
        )
        .unwrap_err();

        assert!(err.contains("aggregated and multi_emit candidates"));
    }

    #[test]
    fn test_multi_emit_candidate_requires_aggregate_fields() {
        let err = ActionCandidate::new(
            "candidate-1".to_string(),
            1_000,
            ActionCandidateType::FileRead,
            ProcessId::new(1, 2),
            serde_json::json!({"path": "/tmp/x"}),
            vec!["action-1".to_string(), "action-2".to_string()],
            CandidateDerivationMode::MultiEmit,
            Lossiness::Lossy,
            None,
            None,
            Some("fanout".to_string()),
            Some(SignalClass::LowSignal),
            None,
        )
        .unwrap_err();

        assert!(err.contains("aggregated and multi_emit candidates"));
    }

    #[test]
    fn test_maps_execve_to_process_exec_candidate() {
        let mut action = make_action("execve", serde_json::json!({"argv": ["/bin/ls", "-l"]}));
        action.assign_agent("agent-1".to_string());

        let candidate = ActionCandidate::from_action_event(&action).unwrap();
        assert_eq!(candidate.candidate_type, ActionCandidateType::ProcessExec);
        assert_eq!(candidate.derivation_mode, CandidateDerivationMode::Direct);
        assert_eq!(candidate.lossiness, Lossiness::Lossy);
        assert_eq!(candidate.raw_event_refs, vec!["action-1".to_string()]);
        assert_eq!(candidate.agent_id.as_deref(), Some("agent-1"));
        assert_eq!(candidate.signal_class, Some(SignalClass::HighSignal));
    }

    #[test]
    fn test_maps_openat_to_file_read_candidate() {
        let action = make_action(
            "openat",
            serde_json::json!({"path": "/tmp/report.txt", "flags": "O_RDONLY"}),
        );

        let candidate = ActionCandidate::from_action_event(&action).unwrap();
        assert_eq!(candidate.candidate_type, ActionCandidateType::FileRead);
        assert_eq!(candidate.lossiness, Lossiness::Lossless);
        assert_eq!(candidate.normalized_args["path"], "/tmp/report.txt");
        assert_eq!(candidate.signal_class, Some(SignalClass::HighSignal));
    }

    #[test]
    fn test_maps_process_spawn_to_internal_housekeeping_signal() {
        let action = make_action(
            "clone",
            serde_json::json!({"child_pid": 99, "flags": "CLONE_VM"}),
        );

        let candidate = ActionCandidate::from_action_event(&action).unwrap();
        assert_eq!(candidate.candidate_type, ActionCandidateType::ProcessSpawn);
        assert_eq!(
            candidate.signal_class,
            Some(SignalClass::InternalHousekeeping)
        );
    }
}
