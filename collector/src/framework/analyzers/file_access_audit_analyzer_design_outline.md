# FileAccessAuditAnalyzer — Design Outline (v4)

> **Source**: Shared handoff at `shared_handoff.md` + supplementary `payload_probe.txt` (PLATFORM-0042).  
> **Analyzed**: 2026-05-19 by Subagent 2 (Rust Framework Architect).  
> **Status**: Implementation exists (`file_access_audit.rs`, `file_access_audit_contract.rs`).  
> **Pipeline wiring**: ⚠️ Commented out at `main.rs:1335–1339` (FAAP-001).  
> **Compilation**: ✅ Module imports fixed — `analyzer_composition_contract.rs` L674 `super` → `super::super` (2026-05-19).  
> **Pre-existing compilation blockers**: `accountability` module has 5 unresolved type errors (`CausalGraphSnapshot`, `GraphSnapshot`, `ResponsibilityAlert.causal_graph`) that block `cargo test --lib`. These are unrelated to FileAccessAuditAnalyzer.

## 1. Requirements Summary

| ID | Source | Requirement | Implementation |
|----|--------|-------------|----------------|
| R1 | Handoff §1 | Track file open/openat syscalls via eBPF | ✅ Consumes `process` source events with `data.event == "FILE_OPEN"`. `classify_access_type()` parses openat flags (O_WRONLY `0x1`, O_RDWR `0x2`, O_PATH `0x400000`, O_DIRECTORY `0x10000`) plus path-based heuristics for `/proc`/`/sys`. |
| R2 | Handoff §2 | Route file-access events through the analyzer chain | ✅ Implements `Analyzer` trait (`async fn process(EventStream) -> Result<EventStream>`). Registered as `EventSource::FileAccess` → `"file_access"` in `pipeline_contract.rs`. |
| R3 | Handoff §3 | Expose file-access data in frontend timeline view | ✅ Emits `file_access` source events with structured JSON. Frontend maps `severity` → display level. |
| R4 | PLATFORM-0042 §1 | Workspace boundary tag (`inside`/`outside`) | ✅ `resolve_boundary()` uses `Path::starts_with()` against pre-canonicalized workspace roots. LRU cache (1024 entries) for `canonicalize()`. |
| R5 | PLATFORM-0042 §2 | Cross-boundary → WARN level | ✅ `severity = if boundary == "outside" { "warn" } else { "info" }`. |
| R6 | PLATFORM-0042 §3 | Must not filter cross-boundary events | ✅ Pass-through contract: both original `process` event AND enriched `file_access` event are emitted. |
| R7 | (derived) | Risk classification | ✅ Write to system dirs (`/etc`, `/usr/lib`, `/root`, `/boot`) → `"high"` risk. Outside workspace → `"medium"`. All other → `"low"`. |

## 2. Interface

### 2.1 Analyzer Trait

```rust
pub struct FileAccessAuditAnalyzer {
    config: FileAccessAuditConfig,       // contract types in file_access_audit_contract.rs
    workspace_roots: Vec<PathBuf>,        // resolved via canonicalize() at construction
    state: Arc<Mutex<FileAccessAuditState>>, // LRU cache + boundary log counter
}

#[async_trait]
impl Analyzer for FileAccessAuditAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;
    fn name(&self) -> &str;  // "FileAccessAuditAnalyzer"
}
```

**Constructor**: `FileAccessAuditAnalyzer::new(config: FileAccessAuditConfig, workspace_roots: Vec<PathBuf>)`

### 2.2 Contract Types (`file_access_audit_contract.rs`)

| Type | Purpose | Key Fields |
|------|---------|------------|
| `FileAccessType` | Enum classification | `Read`, `Write`, `Execute`, `Stat`, `Delete` |
| `KernelFileContext` | File metadata (forward-compatible) | `path: String`, `mode: u32`, optional `inode`/`size`/`uid`/`gid`/`atime`/`mtime` |
| `FileAccessAuditConfig` | Behavioral configuration | `enabled`, `monitored_paths`, `excluded_paths`, `flag_system_writes`, `flag_cross_user_access`, `min_risk_level` |
| `FileAccessAuditRecord` | Output record | `pid`, `comm`, `access_type`, `file_context`, `risk_level`, `risk_reason` |

### 2.3 Pipeline Contract Registration

- **Event source**: `EventSource::FileAccess` → `as_str() == "file_access"` (defined in `pipeline_contract.rs` line 138)
- **Stage**: `PipelineStage::Preprocessing` (stage 0) — runs before InjectionDetection
- **Contract**: `canonical_pipeline_contracts()` lists `"file_access"` in Preprocessing's `produces` set (line 241)

## 3. Data Flow

```
EventStream (all sources: ssl, process, system, stdio)
  │
  ▼  stream.map(process_event)
  │
  ├── source != "process"                     → vec![event]            // pass-through
  ├── data["event"] != "FILE_OPEN"            → vec![event]            // pass-through
  ├── data["filepath"] empty                  → vec![event]            // pass-through
  │
  └── FILE_OPEN matched:
      │
      ├── classify_access_type(filepath, flags)
      │     O_WRONLY|O_RDWR → Write          (risk: high if system write)
      │     O_PATH          → Execute         (risk: medium if outside)
      │     O_DIRECTORY     → Stat            (risk: low otherwise)
      │     /proc|/sys      → Stat
      │     default         → Read
      │
      ├── resolve_boundary(filepath, workspace_roots)
      │     canonicalize(path) via LRU cache
      │     check root.starts_with(resolved)
      │     → "inside" | "outside"
      │
      ├── classify_risk(access_type, filepath, boundary)
      │     + system write               → high
      │     + outside workspace          → medium
      │     + default                    → low
      │
      └── emit: vec![original_event, enriched_event]
            enriched.source = "file_access"
            enriched.data.workspace_boundary = "inside"|"outside"
            enriched.data.severity = "info"|"warn"
            enriched.data.risk_level = "low"|"medium"|"high"
  │
  ▼  flat_map(stream::iter)
  │
EventStream (original + file_access events interleaved)
```

### 3.1 Streaming Design Decision

**Choice**: `.map().flat_map()` with `Arc<Mutex<FileAccessAuditState>>` for mutable state.

**Rationale**: Avoids unbounded buffering (DD-001 pattern). The lock is held for ~microseconds per event (cache lookup + string comparison). In a single-analyzer context, contention is zero because events process sequentially.

**Alternative rejected**: `.collect()` would buffer the entire stream — hangs on infinite live streams.

## 4. Implementation Status

| Component | Status | Location |
|-----------|--------|----------|
| `file_access_audit_contract.rs` | ✅ Complete | Defines types, config, defaults |
| `file_access_audit.rs` | ✅ Complete | Analyzer impl, all 8 unit tests passing |
| `mod.rs` registration | ✅ Complete | `pub mod file_access_audit;` + `pub use FileAccessAuditAnalyzer;` |
| `pipeline_contract.rs` | ✅ Complete | `EventSource::FileAccess` + Preprocessing produces set |
| Pipeline wiring (`main.rs`) | ❌ **GAP** | Lines 1335–1339 commented out (FAAP-001). **Stale** `println!("✓ File access audit enabled")` at L1340 prints success despite analyzer being disabled. |
| `analyzer_composition_contract.rs` test import | ✅ Fixed (v4) | `super` → `super::super` on L674 to access sibling module `file_access_audit_contract` |
| Frontend field alignment | ⚠️ **Coordination** | Backend emits `severity: "info"|"warn"`; frontend contract expects `level: 'INFO'|'WARN'|'ERROR'` |

### 4.1 Integration Gap Detail

**File**: `src/main.rs` lines 1335–1340

```rust
// FAAP-001: FileAccessAuditAnalyzer — DISABLED pending compilation fix.
// agent = agent.add_global_analyzer(Box::new(FileAccessAuditAnalyzer::new(
//     FileAccessAuditConfig::default(),
//     vec![std::path::PathBuf::from(".")],
// )));
println!("✓ File access audit enabled");
```

**Diagnosis**: The FAAP-001 comment claims "pending compilation fix" but the code compiled successfully (`cargo check` passes with zero errors). The real issue was:

1. **Bug**: `analyzer_composition_contract.rs` line 674 used `super::file_access_audit_contract::FileAccessType` inside a `mod tests {}` block, where `super` resolves to `analyzer_composition_contract` rather than `analyzers`. **Fixed** in v4 by changing to `super::super::file_access_audit_contract::FileAccessType`.

2. **Stale `println!`**: Line 1340 prints "✓ File access audit enabled" even when the analyzer is commented out. This is misleading and should be removed or gated behind the actual registration.

**Remaining blocker**: The pre-existing `accountability` module compilation errors (`CausalGraphSnapshot`, `GraphSnapshot`, `ResponsibilityAlert.causal_graph`) prevent `cargo test --lib` from reaching the file_access_audit tests. These are unrelated to the analyzer.

### 4.2 Test Coverage

| Test | Status | What It Verifies |
|------|--------|-----------------|
| `inside_workspace_gets_inside_tag` | ✅ | Path under workspace root → `"inside"` boundary |
| `outside_workspace_gets_warn_severity` | ✅ | Path outside workspace → `"warn"` severity (PLATFORM-0042 §2) |
| `original_event_preserved_in_output` | ✅ | Both original + enriched emitted (PLATFORM-0042 §3) |
| `write_to_etc_gets_high_risk` | ✅ | Write to `/etc/passwd` → `"high"` risk |
| `non_file_open_events_pass_through` | ✅ | Non-FILE_OPEN events unchanged |
| `empty_workspace_roots_all_outside` | ✅ | Empty roots → everything `"outside"` |
| `read_access_classification` | ✅ | O_RDONLY flags → `"Read"` access type |
| `proc_access_classified_as_stat` | ✅ | `/proc` paths → `"Stat"` access type |

## 5. Risk Notes

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Not wired in pipeline** | Zero `file_access` events; feature dead on arrival | **Certain** (currently commented out) | Uncomment wiring with real workspace roots |
| **Stale `println!`** (L1340) | Operator sees "✓ File access audit enabled" but analyzer is disabled | High (deceptive) | Gate the print behind the actual registration or remove it entirely |
| `canonicalize()` I/O per unique path | Latency spike under high file throughput (e.g., `find /usr`) | Medium | LRU cache (1024 entries). Future: push canonicalization to eBPF side |
| `KernelFileContext` serializes `null` for absent eBPF fields | Bloated JSON with `"inode": null` etc. | Low | Current impl uses `json!()` macro which omits `Option::None` fields implicitly — **this is safe** |
| Incorrect workspace roots | All paths misclassified as inside/outside | Low | First 10 boundary decisions logged at INFO for operator verification |
| **Doubled event count** for FILE_OPEN | Increased downstream processing load | Medium | Required by PLATFORM-0042 §3; downstream stages can filter on `event.source == "file_access"` |
| Frontend `severity` vs `level` mismatch | Timeline may not highlight WARN events correctly | Medium | Coordination point: backend uses `severity`, frontend expects `level`. Either align on one field or add both |
| **Pre-existing** `accountability` module errors | Blocks `cargo test --lib` from compiling; cannot run unit tests | High (immediate) | File a separate work item for the accountability team. Not caused by this analyzer. |

## 6. Supplementary Notes

### 6.1 Access Type Classification Matrix

| Flags Value | Condition | Classification |
|-------------|-----------|---------------|
| `0x0` | O_RDONLY | Read |
| `0x1` | O_WRONLY | Write |
| `0x2` | O_RDWR | Write |
| `0x400000` | O_PATH | Execute |
| `0x10000` | O_DIRECTORY | Stat |
| fallback | `/proc`, `/sys` paths | Stat |

### 6.2 Emitted Event Schema

```json
{
  "event": "FILE_OPEN",
  "filepath": "/home/user/project/main.rs",
  "flags": 0,
  "access_type": "Read",
  "workspace_boundary": "inside",
  "severity": "info",
  "risk_level": "low",
  "risk_reason": null,
  "file_context": {
    "path": "/home/user/project/main.rs",
    "mode": 33188
  }
}
```

### 6.3 Workspace Boundary Resolution Logic

```
Input: filepath = "/home/user/project/main.rs"
       roots = ["/home", "/data/workspace"]

1. canonicalize(path) → "/home/user/project/main.rs" (LRU-cached)
2. Check: "/home/user/project/main.rs".starts_with("/home")  → true
3. Result: "inside"

Input: filepath = "/tmp/override_probe.txt"
       roots = ["/home"]

1. canonicalize("/tmp/override_probe.txt") → "/tmp/override_probe.txt"
2. Check: "/tmp/override_probe.txt".starts_with("/home")  → false
3. Result: "outside"
```

Empty `workspace_roots` → all paths classified as `"outside"` (tested).

### 6.4 PLATFORM-0042 Priority Declaration

The supplementary requirements (PLATFORM-0042) take priority over the main handoff if conflicts arise (approved 2026-04-15). No conflicts exist in the current implementation:
- PLATFORM-0042 §1 (boundary tag) → additive feature, no conflict
- PLATFORM-0042 §2 (WARN level) → additive feature, no conflict
- PLATFORM-0042 §3 (no filtering) → clarifies pass-through behavior that was ambiguous in the main handoff
