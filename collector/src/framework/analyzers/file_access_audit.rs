// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project

//! File Access Audit Analyzer
//!
//! Consumes eBPF file-operation events from the "process" event source,
//! enriches them with workspace-boundary classification and risk-level
//! assessment, and emits dedicated "file_access" source events.
//!
//! ## Design
//!
//! - **Workspace boundary** (PLATFORM-0042 §1): Tags each file access as "inside"
//!   or "outside" the configured workspace roots. Uses `std::path::Path::starts_with`
//!   with an LRU cache for pre-resolved canonical paths.
//! - **Cross-boundary WARN** (PLATFORM-0042 §2): Access outside workspace gets
//!   `severity: "warn"`. The original event is always passed through (PLATFORM-0042 §3).
//! - **Risk level**: Write to system dirs → "high", outside workspace → "medium",
//!   all other → "low".
//!
//! ## Consumed Event Sources
//!
//! | Source    | Event type | Fields used                               |
//! |-----------|------------|-------------------------------------------|
//! | `process` | `FILE_OPEN`| `filepath`, `flags` (openat flags), `pid` |
//! | `process` | `EXEC`     | `full_command` (topology tracking)        |
//!
//! ## Emitted Event Source
//!
//! `"file_access"` — enriched events with workspace boundary and risk metadata.

use super::{Analyzer, AnalyzerError};
use crate::framework::analyzers::file_access_audit_contract::{
    FileAccessAuditConfig, FileAccessType, KernelFileContext,
};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use async_trait::async_trait;
use futures::stream::{self, StreamExt};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

/// Maximum number of entries in the canonicalize LRU cache.
const CANONICALIZE_CACHE_SIZE: usize = 1024;

/// System directories that trigger "high" risk level on write access.
const SYSTEM_WRITE_DIRS: &[&str] = &["/etc", "/usr/lib", "/root", "/boot"];

/// Directories excluded from read classification (treated as stat access).
const EXCLUDED_READ_DIRS: &[&str] = &["/proc", "/sys"];

// ─────────────────────────────────────────────────────────────────────────────
// LRU Canonicalize Cache
// ─────────────────────────────────────────────────────────────────────────────

/// A simple bounded cache for `std::fs::canonicalize` results.
///
/// Avoids repeated filesystem I/O for frequently accessed paths. Cache misses
/// fall through to `canonicalize()` — correctness does not depend on the cache.
struct CanonicalizeCache {
    cache: HashMap<PathBuf, PathBuf>,
    access_order: Vec<PathBuf>,
    max_size: usize,
}

impl CanonicalizeCache {
    fn new(max_size: usize) -> Self {
        Self {
            cache: HashMap::new(),
            access_order: Vec::with_capacity(max_size),
            max_size,
        }
    }

    fn get_or_resolve(&mut self, path: &Path) -> PathBuf {
        if let Some(canonical) = self.cache.get(path) {
            return canonical.clone();
        }

        let canonical = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());

        // Evict LRU entry if at capacity
        if self.cache.len() >= self.max_size {
            if let Some(lru) = self.access_order.pop() {
                self.cache.remove(&lru);
            }
        }

        self.cache.insert(path.to_path_buf(), canonical.clone());
        self.access_order.push(path.to_path_buf());

        canonical
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared Mutable State
// ─────────────────────────────────────────────────────────────────────────────

/// Mutable state behind `Arc<Mutex<>>` to enable streaming `.map()` processing.
///
/// The lock is held for ~microseconds per event (cache lookup + boundary check).
/// In a single-analyzer context, contention is zero — the stream processes
/// events sequentially within one analyzer invocation.
struct FileAccessAuditState {
    canonicalize_cache: CanonicalizeCache,
    /// Simple counter for logging first N boundary decisions
    boundary_log_count: u32,
}

impl FileAccessAuditState {
    fn new() -> Self {
        Self {
            canonicalize_cache: CanonicalizeCache::new(CANONICALIZE_CACHE_SIZE),
            boundary_log_count: 0,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Analyzer
// ─────────────────────────────────────────────────────────────────────────────

/// Analyzer that enriches file access events with workspace boundary and risk data.
pub struct FileAccessAuditAnalyzer {
    config: FileAccessAuditConfig,
    workspace_roots: Vec<PathBuf>,
    /// Mutable state shared with the streaming closure.
    state: Arc<Mutex<FileAccessAuditState>>,
}

impl FileAccessAuditAnalyzer {
    /// Create a new `FileAccessAuditAnalyzer`.
    ///
    /// `workspace_roots` defines the "inside" workspace. Paths that don't descend
    /// from any root are marked "outside". Roots are resolved via `canonicalize()`
    /// once at construction time.
    pub fn new(config: FileAccessAuditConfig, workspace_roots: Vec<PathBuf>) -> Self {
        let resolved_roots: Vec<PathBuf> = workspace_roots
            .iter()
            .map(|root| std::fs::canonicalize(root).unwrap_or_else(|_| root.clone()))
            .collect();

        log::info!(
            "FileAccessAuditAnalyzer initialized with {} workspace root(s): {:?}",
            resolved_roots.len(),
            resolved_roots
        );

        Self {
            config,
            workspace_roots: resolved_roots,
            state: Arc::new(Mutex::new(FileAccessAuditState::new())),
        }
    }

    /// Classify the file access type from openat flags.
    ///
    /// Falls back to filepath heuristics when flags are absent or zero
    /// (common for stat-family syscalls).
    fn classify_access_type(filepath: &str, flags: u64) -> FileAccessType {
        // Check for write flags: O_WRONLY (0x1) or O_RDWR (0x2)
        if (flags & 0x1) != 0 || (flags & 0x2) != 0 {
            return FileAccessType::Write;
        }

        // Check for execute: O_PATH (0x400000) or __O_EXEC
        if (flags & 0x400000) != 0 {
            return FileAccessType::Execute;
        }

        // Check for directory/stat: O_DIRECTORY (0x10000)
        if (flags & 0x10000) != 0 {
            return FileAccessType::Stat;
        }

        // Fallback heuristics for absent flags
        if filepath.starts_with("/proc") || filepath.starts_with("/sys") {
            return FileAccessType::Stat;
        }

        // Default: O_RDONLY (0x0) or unknown → Read
        FileAccessType::Read
    }

    /// Resolve the workspace boundary for a filepath.
    ///
    /// Uses `Path::starts_with` against pre-resolved workspace roots.
    /// Symlinks are canonicalized via the LRU cache behind the mutex.
    fn resolve_boundary(filepath: &str, workspace_roots: &[PathBuf], state: &Arc<Mutex<FileAccessAuditState>>) -> &'static str {
        let path = Path::new(filepath);
        let inside = {
            let mut guard = state.lock().expect("FileAccessAuditState lock poisoned");
            let resolved = guard.canonicalize_cache.get_or_resolve(path);
            let inside = workspace_roots.iter().any(|root| resolved.starts_with(root));

            // Log first 10 boundary decisions for operator verification
            if guard.boundary_log_count < 10 {
                log::info!(
                    "FileAccessAudit: boundary={} path={}",
                    if inside { "inside" } else { "outside" },
                    filepath
                );
                guard.boundary_log_count += 1;
            }

            inside
        };

        if inside { "inside" } else { "outside" }
    }

    /// Classify risk level for a file access.
    fn classify_risk(access_type: FileAccessType, filepath: &str, boundary: &str, config: &FileAccessAuditConfig) -> (&'static str, Option<String>) {
        // High risk: writes to system directories
        if access_type == FileAccessType::Write && config.flag_system_writes {
            for sys_dir in SYSTEM_WRITE_DIRS {
                if filepath.starts_with(sys_dir) {
                    return ("high", Some(format!("Write to system directory: {}", sys_dir)));
                }
            }
        }

        // Medium risk: any access outside workspace
        if boundary == "outside" {
            return ("medium", Some("Access outside workspace boundary".to_string()));
        }

        // Low risk: all other access
        ("low", None)
    }

    /// Process a single FILE_OPEN event from the process source.
    fn process_file_open(event: &Event, config: &FileAccessAuditConfig, workspace_roots: &[PathBuf], state: &Arc<Mutex<FileAccessAuditState>>) -> Vec<Event> {
        let filepath = event
            .data
            .get("filepath")
            .and_then(Value::as_str)
            .unwrap_or("");

        if filepath.is_empty() {
            return vec![event.clone()];
        }

        let flags = event.data.get("flags").and_then(Value::as_u64).unwrap_or(0);

        let access_type = Self::classify_access_type(filepath, flags);
        let boundary = Self::resolve_boundary(filepath, workspace_roots, state);
        let (risk_level, risk_reason) = Self::classify_risk(access_type, filepath, boundary, config);
        let severity = if boundary == "outside" { "warn" } else { "info" };

        // Build the file_context from available eBPF fields
        let file_context = KernelFileContext {
            path: filepath.to_string(),
            inode: None,        // not emitted by current eBPF probe
            size: None,         // not emitted by current eBPF probe
            mode: event.data.get("flags").and_then(Value::as_u64).unwrap_or(0) as u32,
            uid: None,          // not emitted by current eBPF probe
            gid: None,          // not emitted by current eBPF probe
            atime: None,        // not emitted by current eBPF probe
            mtime: None,        // not emitted by current eBPF probe
        };

        // Build the file_access audit event
        let audit_event = Event::new_with_timestamp(
            event.timestamp,
            "file_access".to_string(),
            event.pid,
            event.comm.clone(),
            json!({
                "event": "FILE_OPEN",
                "filepath": filepath,
                "flags": flags,
                "access_type": format!("{:?}", access_type),
                "workspace_boundary": boundary,
                "severity": severity,
                "risk_level": risk_level,
                "risk_reason": risk_reason,
                "file_context": {
                    "path": file_context.path,
                    "mode": file_context.mode,
                },
            }),
        );

        // Pass-through contract: emit both original + enriched (PLATFORM-0042 §3)
        vec![event.clone(), audit_event]
    }
}

#[async_trait]
impl Analyzer for FileAccessAuditAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        // Use streaming `.map().flat_map()` instead of eager `collect()` to avoid
        // hanging on infinite live streams (DD-001).
        //
        // Mutable state (canonicalize_cache, boundary_log_count) is behind
        // `Arc<Mutex<>>` so the closure can capture it without `&mut self` issues.
        let config = self.config.clone();
        let workspace_roots = self.workspace_roots.clone();
        let state = Arc::clone(&self.state);

        let processed_stream = stream
            .map(move |event| {
                // Only process FILE_OPEN events from the process source
                if event.source == "process" {
                    let event_name = event
                        .data
                        .get("event")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    if event_name == "FILE_OPEN" {
                        return Self::process_file_open(&event, &config, &workspace_roots, &state);
                    }
                }

                // Pass-through all other events unchanged
                vec![event]
            })
            .flat_map(stream::iter);

        Ok(Box::pin(processed_stream))
    }

    fn name(&self) -> &str {
        "FileAccessAuditAnalyzer"
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use futures::stream::StreamExt;

    fn mk_event(timestamp: u64, source: &str, pid: u32, comm: &str, data: Value) -> Event {
        Event::new_with_timestamp(timestamp, source.to_string(), pid, comm.to_string(), data)
    }

    #[tokio::test]
    async fn inside_workspace_gets_inside_tag() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            100,
            "claude",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/home/user/project/main.rs",
                "flags": 0, // O_RDONLY
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let file_access = output.iter().find(|e| e.source == "file_access");
        assert!(file_access.is_some(), "expected file_access event");

        let ev = file_access.unwrap();
        assert_eq!(
            ev.data.get("workspace_boundary").and_then(Value::as_str),
            Some("inside")
        );
        assert_eq!(
            ev.data.get("severity").and_then(Value::as_str),
            Some("info")
        );
    }

    #[tokio::test]
    async fn outside_workspace_gets_warn_severity() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            100,
            "claude",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/tmp/override_probe.txt",
                "flags": 0, // O_RDONLY
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let file_access = output.iter().find(|e| e.source == "file_access");
        assert!(file_access.is_some(), "expected file_access event");

        let ev = file_access.unwrap();
        assert_eq!(
            ev.data.get("workspace_boundary").and_then(Value::as_str),
            Some("outside")
        );
        assert_eq!(
            ev.data.get("severity").and_then(Value::as_str),
            Some("warn")
        );
    }

    #[tokio::test]
    async fn original_event_preserved_in_output() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            100,
            "claude",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/home/user/project/main.rs",
                "flags": 0,
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let original = output.iter().find(|e| e.source == "process");
        assert!(original.is_some(), "original process event should be in output");
        assert_eq!(output.len(), 2, "should emit original + file_access events");
    }

    #[tokio::test]
    async fn write_to_etc_gets_high_risk() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            200,
            "sudo",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/etc/passwd",
                "flags": 577, // O_WRONLY|O_CREAT|O_TRUNC
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let file_access = output.iter().find(|e| e.source == "file_access");
        assert!(file_access.is_some(), "expected file_access event");

        let ev = file_access.unwrap();
        assert_eq!(
            ev.data.get("risk_level").and_then(Value::as_str),
            Some("high")
        );
        assert_eq!(
            ev.data.get("access_type").and_then(Value::as_str),
            Some("Write")
        );
    }

    #[tokio::test]
    async fn non_file_open_events_pass_through_unchanged() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            300,
            "claude",
            json!({
                "event": "EXEC",
                "ppid": 1,
                "full_command": "git status",
                "filename": "/usr/bin/git"
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        // Only the original event, no file_access event
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].source, "process");
    }

    #[tokio::test]
    async fn empty_workspace_roots_all_outside() {
        let config = FileAccessAuditConfig::default();
        let roots: Vec<PathBuf> = vec![];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            400,
            "claude",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/home/user/file.txt",
                "flags": 0,
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let file_access = output.iter().find(|e| e.source == "file_access");
        assert!(file_access.is_some());

        let ev = file_access.unwrap();
        assert_eq!(
            ev.data.get("workspace_boundary").and_then(Value::as_str),
            Some("outside"),
            "empty workspace roots should classify everything as outside"
        );
    }

    #[tokio::test]
    async fn read_access_classification() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            500,
            "claude",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/home/user/readme.txt",
                "flags": 0, // O_RDONLY
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let file_access = output.iter().find(|e| e.source == "file_access");
        assert!(file_access.is_some());

        let ev = file_access.unwrap();
        assert_eq!(
            ev.data.get("access_type").and_then(Value::as_str),
            Some("Read")
        );
        assert_eq!(
            ev.data.get("risk_level").and_then(Value::as_str),
            Some("low")
        );
    }

    #[tokio::test]
    async fn proc_access_classified_as_stat() {
        let config = FileAccessAuditConfig::default();
        let roots = vec![PathBuf::from("/home")];
        let mut analyzer = FileAccessAuditAnalyzer::new(config, roots);

        let input = vec![mk_event(
            1_000,
            "process",
            600,
            "monitor",
            json!({
                "event": "FILE_OPEN",
                "count": 1,
                "filepath": "/proc/self/status",
                "flags": 0,
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let file_access = output.iter().find(|e| e.source == "file_access");
        assert!(file_access.is_some());

        let ev = file_access.unwrap();
        assert_eq!(
            ev.data.get("access_type").and_then(Value::as_str),
            Some("Stat")
        );
    }
}
