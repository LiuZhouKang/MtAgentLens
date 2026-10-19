// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! File Access Audit Analyzer Contract
//!
//! Defines the interface for auditing file access patterns with kernel enrichment.
//! Consumes file operation events from eBPF and produces audit records with
//! process context, file metadata, and access classification.

use serde::{Deserialize, Serialize};

/// Input event classification for file access audit
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FileAccessType {
    /// File opened for reading
    Read,
    /// File opened for writing
    Write,
    /// File opened for execution
    Execute,
    /// File metadata accessed (stat, access, etc)
    Stat,
    /// File deleted or unlinked
    Delete,
}

/// Kernel enrichment data required from eBPF
///
/// ## Field availability
///
/// The current eBPF process probe emits only `path` and `mode`.
/// Fields marked `Option` (inode, size, uid, gid, atime, mtime) are
/// aspirational — they are defined here for forward compatibility but
/// will be `None` (omitted from output) until the eBPF probe is extended.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KernelFileContext {
    /// Absolute file path
    pub path: String,
    /// File inode number (not emitted by current eBPF probe)
    pub inode: Option<u64>,
    /// File size in bytes (not emitted by current eBPF probe)
    pub size: Option<u64>,
    /// File permissions (mode) — available from eBPF mode field
    pub mode: u32,
    /// User ID of file owner (not emitted by current eBPF probe)
    pub uid: Option<u32>,
    /// Group ID of file owner (not emitted by current eBPF probe)
    pub gid: Option<u32>,
    /// Access time in unix timestamp (not emitted by current eBPF probe)
    pub atime: Option<u64>,
    /// Modification time in unix timestamp (not emitted by current eBPF probe)
    pub mtime: Option<u64>,
}

/// Output audit record
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileAccessAuditRecord {
    /// Process ID performing access
    pub pid: u32,
    /// Process command name
    pub comm: String,
    /// User ID of process
    pub uid: u32,
    /// Type of file access
    pub access_type: FileAccessType,
    /// Kernel-enriched file context
    pub file_context: KernelFileContext,
    /// Risk classification (low/medium/high)
    pub risk_level: String,
    /// Reason for risk classification
    pub risk_reason: Option<String>,
}

/// Configuration for file access audit analyzer
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileAccessAuditConfig {
    /// Enable audit logging
    pub enabled: bool,
    /// Path prefixes to audit. Empty means every path is audited.
    pub monitored_paths: Vec<String>,
    /// Path prefixes to exclude from auditing.
    pub excluded_paths: Vec<String>,
    /// Flag suspicious writes to system directories
    pub flag_system_writes: bool,
    /// Minimum risk level to emit: `low`, `medium` or `high`.
    pub min_risk_level: String,
}

impl FileAccessAuditConfig {
    /// Load configuration overrides from `MTAGENTLENS_FILE_AUDIT_*`.
    ///
    /// Defaults preserve the pre-wiring behaviour (audit everything, report
    /// from `low`). Without this constructor the wired fields would be
    /// unreachable from production, which is the same no-op in a new costume.
    pub fn from_env() -> Self {
        let mut config = Self::default();
        if let Ok(raw) = std::env::var("MTAGENTLENS_FILE_AUDIT_ENABLED") {
            config.enabled = matches!(raw.trim(), "1" | "true" | "yes");
        }
        if let Ok(raw) = std::env::var("MTAGENTLENS_FILE_AUDIT_MONITORED") {
            config.monitored_paths = parse_path_list(&raw);
        }
        if let Ok(raw) = std::env::var("MTAGENTLENS_FILE_AUDIT_EXCLUDED") {
            config.excluded_paths = parse_path_list(&raw);
        }
        if let Ok(raw) = std::env::var("MTAGENTLENS_FILE_AUDIT_MIN_RISK") {
            let level = raw.trim().to_ascii_lowercase();
            if matches!(level.as_str(), "low" | "medium" | "high") {
                config.min_risk_level = level;
            }
        }
        config
    }
}

fn parse_path_list(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

impl Default for FileAccessAuditConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            // Defaults preserve the pre-wiring behaviour: audit every path and
            // emit every risk level. Narrowing the scope is opt-in.
            monitored_paths: vec![],
            excluded_paths: vec![],
            flag_system_writes: true,
            min_risk_level: "low".to_string(),
        }
    }
}

#[cfg(test)]
mod env_config_tests {
    use super::*;

    #[test]
    fn path_list_trims_and_drops_blanks() {
        assert_eq!(
            parse_path_list(" /etc , /root ,, /home/user "),
            vec![
                "/etc".to_string(),
                "/root".to_string(),
                "/home/user".to_string()
            ]
        );
        assert!(parse_path_list("  ").is_empty());
    }

    #[test]
    fn defaults_preserve_pre_wiring_behaviour() {
        let config = FileAccessAuditConfig::default();
        assert!(config.enabled);
        assert!(config.monitored_paths.is_empty());
        assert!(config.excluded_paths.is_empty());
        assert_eq!(config.min_risk_level, "low");
    }
}
