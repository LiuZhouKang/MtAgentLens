// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// action_classifier.rs — Single entry point for classifying syscalls into Action types.
//
// Maps raw syscall events (from eBPF or framework layer) into typed Action
// structs with normalized arguments. This is the sole syscall→ActionType
// entry point for the entire evidence pipeline.

use crate::responsibility::evidence::args::extract_action_args;
use crate::responsibility::types::{
    Action, ActionCandidateType, ActionId, CandidateDerivationMode, ProcessId, SignalClass,
};

// ─── ActionClassifier ─────────────────────────────────────────────────

pub struct ActionClassifier;

impl ActionClassifier {
    /// Classify a syscall into an Action, or return `None` if the syscall
    /// is not actionable.
    ///
    /// `raw_args` are the string-form arguments extracted from the syscall
    /// event: for file ops this is the path; for execve this is argv[0];
    /// for connect this is the destination address.
    pub fn classify(
        syscall: &str,
        raw_args: &[String],
        process_id: ProcessId,
        timestamp_ns: u64,
    ) -> Option<Action> {
        let action_type = Self::syscall_to_type(syscall, raw_args)?;
        let signal_class = Self::signal_class_for(&action_type);
        let normalized_args = extract_action_args(&action_type, raw_args);

        Some(Action {
            outcome: crate::responsibility::types::ActionOutcome::Unknown,
            syscall_ret: None,
            syscall_name: Some(syscall.to_string()),
            action_id: ActionId::new(),
            process_id,
            action_type,
            normalized_args,
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class,
            timestamp_ns,
        })
    }

    /// Map a syscall name and its raw args to an ActionCandidateType.
    ///
    /// Returns `None` for syscalls that are not relevant to the evidence
    /// pipeline (e.g., getpid, mmap, etc.).
    fn syscall_to_type(syscall: &str, _raw_args: &[String]) -> Option<ActionCandidateType> {
        match syscall {
            // ── File open variants — distinguish read/write/overwrite by flags ──
            "openat" | "open" | "openat2" => Some(ActionCandidateType::FileOpen),

            // ── Read syscalls ──
            "read" | "pread64" | "preadv" | "readv" => Some(ActionCandidateType::FileRead),

            // ── Write syscalls ──
            "write" | "pwrite64" | "pwritev" | "writev" => Some(ActionCandidateType::FileWrite),

            // ── File deletion ──
            "unlink" | "unlinkat" | "rmdir" => Some(ActionCandidateType::FileDelete),

            // ── File rename ──
            // Both paths are the action's resources: the source (what moved)
            // and the destination (where it went). Keeping the destination
            // only in the raw event meant every downstream claim could answer
            // "what was moved" but never "to where".
            "rename" | "renameat" | "renameat2" => Some(ActionCandidateType::FileRename),

            // ── Network ──
            "connect" => Some(ActionCandidateType::NetworkConnect),
            "sendto" | "sendmsg" | "sendmmsg" => Some(ActionCandidateType::NetworkSend),

            // ── Process execution ──
            "execve" | "execveat" => Some(ActionCandidateType::ProcessExec),

            // ── Process spawn (fork/clone) ──
            "clone" | "clone3" | "fork" | "vfork" => Some(ActionCandidateType::ProcessSpawn),

            // ── Not actionable ──
            _ => None,
        }
    }

    /// Assign a signal class to each action type.
    fn signal_class_for(action_type: &ActionCandidateType) -> SignalClass {
        match action_type {
            ActionCandidateType::FileOpen
            | ActionCandidateType::FileRead
            | ActionCandidateType::FileWrite
            | ActionCandidateType::FileOverwrite
            | ActionCandidateType::FileDelete
            | ActionCandidateType::FileRename
            | ActionCandidateType::NetworkConnect
            | ActionCandidateType::NetworkSend
            | ActionCandidateType::ProcessExec => SignalClass::HighSignal,

            ActionCandidateType::ProcessSpawn => SignalClass::LowSignal,
        }
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn pid() -> ProcessId {
        ProcessId::new(100, 1)
    }

    #[test]
    fn test_openat_readonly() {
        let action = ActionClassifier::classify(
            "openat",
            &["/etc/hosts".to_string(), "0".to_string()],
            pid(),
            1000,
        );
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileOpen);
    }

    #[test]
    fn test_openat_write_create() {
        // O_WRONLY | O_CREAT = 0o101 = 65
        let action = ActionClassifier::classify(
            "openat",
            &["/tmp/newfile".to_string(), "65".to_string()],
            pid(),
            1000,
        );
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileOpen);
    }

    #[test]
    fn test_openat_overwrite() {
        // O_RDWR | O_CREAT | O_TRUNC = 0o1102 = 578
        let action = ActionClassifier::classify(
            "openat",
            &["/tmp/overwrite".to_string(), "578".to_string()],
            pid(),
            1000,
        );
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileOpen);
    }

    #[test]
    fn test_read_syscall() {
        let action = ActionClassifier::classify("read", &["3".to_string()], pid(), 1000);
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileRead);
    }

    /// A rename is not an overwrite: the source disappears and the destination
    /// appears. Recording it as FileOverwrite loses the destination entirely,
    /// which is the one fact an accountability trail most needs ("where did
    /// this file go?"). Both paths stay in the action's normalized args.
    #[test]
    fn test_rename_is_its_own_type_with_both_paths() {
        use crate::responsibility::types::NormalizedArg;

        let action = ActionClassifier::classify(
            "rename",
            &["/tmp/src.txt".to_string(), "/tmp/dst.txt".to_string()],
            pid(),
            1000,
        )
        .expect("rename is actionable");

        assert_eq!(action.action_type, ActionCandidateType::FileRename);
        assert_eq!(
            action.normalized_args,
            vec![
                NormalizedArg::Path("/tmp/src.txt".to_string()),
                NormalizedArg::Path("/tmp/dst.txt".to_string()),
            ]
        );
    }

    #[test]
    fn test_write_syscall() {
        let action = ActionClassifier::classify("write", &["1".to_string()], pid(), 1000);
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileWrite);
    }

    #[test]
    fn test_unlink_delete() {
        let action =
            ActionClassifier::classify("unlink", &["/tmp/todelete".to_string()], pid(), 1000);
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileDelete);
    }

    #[test]
    fn test_connect_network() {
        let action =
            ActionClassifier::classify("connect", &["127.0.0.1:8080".to_string()], pid(), 1000);
        assert!(action.is_some());
        assert_eq!(
            action.unwrap().action_type,
            ActionCandidateType::NetworkConnect
        );
    }

    #[test]
    fn test_execve_process_exec() {
        let action = ActionClassifier::classify(
            "execve",
            &["/usr/bin/python3".to_string(), "script.py".to_string()],
            pid(),
            1000,
        );
        assert!(action.is_some());
        assert_eq!(
            action.unwrap().action_type,
            ActionCandidateType::ProcessExec
        );
    }

    #[test]
    fn test_fork_process_spawn() {
        let action = ActionClassifier::classify("clone", &[], pid(), 1000);
        assert!(action.is_some());
        assert_eq!(
            action.unwrap().action_type,
            ActionCandidateType::ProcessSpawn
        );
    }

    #[test]
    fn test_not_actionable() {
        let action = ActionClassifier::classify("getpid", &[], pid(), 1000);
        assert!(action.is_none());
    }

    #[test]
    fn test_sendto_network_send() {
        let action =
            ActionClassifier::classify("sendto", &["10.0.0.1:443".to_string()], pid(), 1000);
        assert!(action.is_some());
        assert_eq!(
            action.unwrap().action_type,
            ActionCandidateType::NetworkSend
        );
    }

    #[test]
    fn test_openat_wronly() {
        // O_WRONLY = 1
        let action = ActionClassifier::classify(
            "openat",
            &["/tmp/out.log".to_string(), "1".to_string()],
            pid(),
            1000,
        );
        assert!(action.is_some());
        assert_eq!(action.unwrap().action_type, ActionCandidateType::FileOpen);
    }

    #[test]
    fn test_classify_with_process_id() {
        let pid = ProcessId::new(42, 1000);
        let action = ActionClassifier::classify(
            "openat",
            &["/some/path".to_string(), "0".to_string()],
            pid,
            2000,
        );
        assert!(action.is_some());
        let a = action.unwrap();
        assert_eq!(a.process_id, pid);
        assert_eq!(a.timestamp_ns, 2000);
        // Action should have a non-empty action_id
        assert!(!a.action_id.as_str().is_empty());
    }
}
