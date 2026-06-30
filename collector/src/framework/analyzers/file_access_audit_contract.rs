// SPDX-License-Identifier: MIT
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
    /// Paths to monitor (glob patterns)
    pub monitored_paths: Vec<String>,
    /// Paths to exclude (glob patterns)
    pub excluded_paths: Vec<String>,
    /// Flag suspicious writes to system directories
    pub flag_system_writes: bool,
    /// Flag cross-user access attempts
    pub flag_cross_user_access: bool,
    /// Minimum risk level to emit (low/medium/high)
    pub min_risk_level: String,
}

impl Default for FileAccessAuditConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            monitored_paths: vec!["/etc".to_string(), "/root".to_string(), "/home".to_string()],
            excluded_paths: vec!["/proc".to_string(), "/sys".to_string()],
            flag_system_writes: true,
            flag_cross_user_access: true,
            min_risk_level: "medium".to_string(),
        }
    }
}
