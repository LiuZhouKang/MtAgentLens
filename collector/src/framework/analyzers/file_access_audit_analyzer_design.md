# FileAccessAuditAnalyzer — Rust Design Outline

## 1. Overview

A streaming analyzer that consumes eBPF file-operation events from the
"process" event source, enriches them with workspace-boundary classification
and the PLATFORM-0042 cross-boundary WARN level, and emits dedicated
"file_access" source events for the frontend timeline.

## 2. Interface

```rust
/// Composition of the existing contract types plus the workspace-boundary tag.
pub struct FileAccessAuditAnalyzer {
    config: FileAccessAuditConfig,
    /// Cache of workspace roots for O(1) boundary checks.
    workspace_roots: Vec<PathBuf>,
}
```

### Consumed event sources

| Source       | Event type | Fields read                          |
|--------------|------------|--------------------------------------|
| `process`    | `FILE_OPEN`| `filepath`, `flags` (O_RDONLY/O_WRONLY etc.) |
| `process`    | `EXEC`     | `full_command` (for cwd resolution)  |

### Emitted event source

`file_access` — enriched events carrying this shape in `data`:

```rust
{
    "event": "FILE_OPEN",
    "filepath": "/absolute/path",
    "access_type": "Read|Write|Execute|Stat|Delete",
    "risk_level": "low|medium|high",
    "workspace_boundary": "inside|outside",
    "severity": "info|warn",     // warn iff workspace_boundary == "outside"
    "file_context": {
        "path": "/absolute/path",
        "mode": 0o644,
        "inode": null,       // not emitted by current eBPF probe; Option<u64>
        "size": null,        // not emitted by current eBPF probe; Option<u64>
        "uid": null,         // not emitted by current eBPF probe; Option<u32>
        "gid": null,         // not emitted by current eBPF probe; Option<u32>
        "atime": null,       // not emitted by current eBPF probe; Option<u64>
        "mtime": null        // not emitted by current eBPF probe; Option<u64>
    }
}
```

### Pass-through contract

The original `process` event is forwarded unchanged alongside each new
`file_access` event. This satisfies the PLATFORM-0042 constraint that
cross-boundary events are never filtered — the original event is always
present in the stream.

### Constructor

```rust
impl FileAccessAuditAnalyzer {
    /// `workspace_roots` — one or more directories defining the "inside"
    /// workspace (e.g. ["/home/user/project", "/data/workspace"]).
    /// Paths that do not descend from any of these are marked "outside".
    pub fn new(config: FileAccessAuditConfig, workspace_roots: Vec<PathBuf>) -> Self;
}
```

## 3. Data flow

```
EventStream (process source, FILE_OPEN events)
  │
  ▼
FileAccessAuditAnalyzer::process()
  │
  ├── For each event:
  │     1. Extract filepath + flags from event.data
  │     2. Classify access_type from flags
  │        - O_RDONLY          → Read
  │        - O_WRONLY|O_RDWR   → Write
  │        - O_EXEC / X_OK     → Execute
  │        - O_DIRECTORY|stat  → Stat
  │        - unlink            → Delete
  │
  │     3. Resolve workspace_boundary:
  │        - Canonicalize filepath via std::fs::canonicalize
  │        - Check against workspace_roots
  │        - If matches any root → "inside"
  │        - Else                → "outside"
  │
  │     4. Enrich with severity + risk_level:
  │        - workspace_boundary == "outside" → severity: "warn", risk_level: "medium"
  │        - Write to system dir (/etc, /usr/lib) → risk_level: "high"
  │        - Else → severity: "info", risk_level: "low"
  │
  │     5. Produce (original_event, file_access_event) tuple
  │
  └── flatten → EventStream (output)
```

## 4. Key implementation decisions

### 4a. Workspace-boundary detection

Use `std::path::Path::starts_with` against the configured workspace roots.
For symlinked paths, `canonicalize()` first. This is O(path depth) per event
and performs no I/O beyond the initial canonicalization (the roots are
pre-resolved in the constructor).

### 4b. Access type from flags

Parse the raw `flags` integer from the eBPF `openat` syscall:
- Bitwise `O_WRONLY` (0x1) or `O_RDWR` (0x2) → Write
- `O_RDONLY` (0x0) → Read
- `O_EXEC` (architecture-defined, typically via `__O_EXEC` or `O_PATH`) → Execute
- Other → Stat

Where eBPF does not provide flags (e.g. older kernels), fall back to
filepath heuristics (/proc, /sys → Stat; /etc → Read).

### 4c. Cross-boundary WARN — no filtering

PLATFORM-0042 §3 explicitly requires pass-through. The analyzer emits the
*original* event alongside the enriched `file_access` event. Downstream
analyzers and the frontend receive both.

### 4d. Risk level logic

| Condition                            | risk_level |
|--------------------------------------|------------|
| Write to /etc, /usr/lib, /root       | high       |
| Any access outside workspace         | medium     |
| All other access                     | low        |

This matches the `FileAccessAuditConfig.flag_system_writes` and
`min_risk_level` defaults from the existing contract.

### 4e. Canonicalize LRU cache

`std::fs::canonicalize()` performs filesystem I/O per unique path, which
can cause latency spikes under high file-open throughput. Mitigate with a
small LRU cache of recently-canonicalized paths (1024 entries). This avoids
repeated stat() calls for frequently accessed files within a workspace.
A cache miss falls through to `canonicalize()` — correctness does not depend
on the cache.

Future optimization: move boundary resolution to the eBPF side by passing
the workspace root at BPF load time, eliminating userspace canonicalize
entirely.

### 4f. Integration with pipeline_contract.rs

Add a new variant to `EventSource`:

```rust
pub enum EventSource {
    // … existing variants …
    FileAccess,
}
```

With `FileAccess.as_str() → "file_access"` and the corresponding
`from_str()` arm.

Add `FileAccess` to the `consumes` set of the `InjectionDetection` stage
contract (it already reads `FILE_OPEN` from the `process` source; adding
`file_access` gives it pre-enriched data as an alternative path).

Register `"file_access"` in the `Preprocessing` stage's `produces` set.

## 5. Registering in the analyzer module

Edit `analyzers/mod.rs`:

```rust
pub mod file_access_audit;
pub use file_access_audit::FileAccessAuditAnalyzer;
```

## 6. Test strategy

| Test case                                        | Checks |
|--------------------------------------------------|--------|
| Inside-workspace file open → `"inside"` tag      | Boundary classification |
| Outside-workspace file open → `"warn"` severity  | PLATFORM-0042 §2 |
| Outside-workspace file still passes original     | PLATFORM-0042 §3 pass-through |
| Write to /etc → `"high"` risk_level              | System-write flagging |
| Original process event is preserved in output    | Pass-through contract |
| Empty workspace_roots → all paths "outside"      | Edge case |
| Symlinked path resolves correctly                | canonicalize coverage |

## 7. Risk notes

| Risk | Impact | Mitigation |
|------|--------|------------|
| `canonicalize()` performs filesystem I/O per event | Latency spike under high file-open throughput | LRU cache (1024 entries) for pre-resolved canonical paths. Future: move boundary logic to eBPF side |
| `workspace_roots` configured incorrectly | All paths misclassified as inside/outside | Log the first 10 boundary decisions at INFO for operator verification |
| Process event source may already contain enriched file events from SecuritySignoffAnalyzer | Duplicate file_access events | FileAccessAuditAnalyzer must run *before* SecuritySignoffAnalyzer. Future: switch SecuritySignoffAnalyzer to consume `file_access` source |
| `KernelFileContext` contract types promise fields eBPF doesn't emit (inode, size, uid, gid, atime, mtime) | Compile-time or runtime confusion | Those fields are now `Option<T>` and omitted from output when absent. Update `file_context` emission to serialize only `Some` fields |
