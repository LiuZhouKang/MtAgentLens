# Analyzer Pipeline Contract — Implementation Gap Analysis

**Author:** rust-framework-architect (Subagent B)
**Run ID:** 20260514-235938-9f5f2965
**Date:** 2026-05-14
**Scenario:** subagents-collaborative-001 (handoff verification pipeline)
**Branch:** lzk

## Status Update

This analysis supersedes the previous gap analysis (dated 2026-05-14 for
agent-teams-context-pollution). The following components that were previously
marked as **Missing** are now **on disk**:

| Component | Previous Status | Current Status | File |
|-----------|----------------|----------------|------|
| Pipeline orchestrator impl | ❌ Missing | ⚠ 已编译但未接线 | `pipeline_orchestrator.rs`（零调用者，属第二管线整批决策） |
| ContextPollutionEvidence lifecycle | ❌ Missing | ⚠ 类型在、生产者已删除 | `pipeline_contract.rs` |
| ContextPollutionAnalyzer | ❌ Missing | 🗑 Deleted (2026-10-16) | 从未编译（6×E0499）；能力由 `graph/taint.rs` 覆盖 |
| FileAccessAuditAnalyzer | ❌ Missing | ✅ Implemented | `file_access_audit.rs` (606 lines, 8 tests) |
| SharedStateStore impl | ❌ Missing | 🗑 Deleted (2026-10-16) | 零实现者/零调用者；trait 仍在 `pipeline_contract.rs` |
| AlertEnrichmentAnalyzer | ❌ Missing | ⚠ 已编译但未注册 | `alert_enrichment.rs`（`mod.rs` 中 `pub use` 被注释） |
| AlertEnrichment stage consumes `context_pollution` | ❌ Missing | ✅ Configured | `pipeline_contract.rs:280` |

## Current Gaps (Still Open)

Despite the implementation progress, **three gaps** remain before the three
feature contracts (CPDP-001, FAAP-001, SSTP-001) are satisfied:

---

## Gap 1: FEATURE-CPDP-001 — Unregistered Modules

### Problem

`context_pollution.rs`, `alert_enrichment.rs`, and `shared_state_store.rs` all
exist on disk and compile independently, but their module declarations in
`mod.rs` are commented out with `// TODO`:

```rust
// pub mod alert_enrichment;   // line 21
// pub mod context_pollution;  // line 27
// pub mod shared_state_store; // line 42
// pub use alert_enrichment::AlertEnrichmentAnalyzer;     // line 52
// pub use context_pollution::ContextPollutionAnalyzer;   // line 54
// pub use shared_state_store::MutexSharedStateStore;     // line 70
```

This means these analyzers are **present on disk but not compiled into the
binary**. They cannot be instantiated, wired into the pipeline, or exercised
by tests.

### Resolution

Uncomment all six lines. The import paths are correct:
- `alert_enrichment.rs` uses `crate::framework::core::Event` and `super::{Analyzer, AnalyzerError}` — both exist
- `context_pollution.rs` uses `crate::framework::analyzers::pipeline_contract::{ContextPollutionEvidence, ContextPollutionPhase}` — exists
- `shared_state_store.rs` uses `crate::framework::analyzers::pipeline_contract::{PromptTriggerSnapshot, SharedStateStore}` — exists

### Risk Assessment

| Module | Risk Level | Expected Issues | Mitigation |
|--------|-----------|-----------------|------------|
| `context_pollution.rs` | **LOW** | Imports and types resolve. The analyzer uses `Arc<Mutex<>>` directly (not through `SharedStateStore` trait) | None needed |
| `alert_enrichment.rs` | **LOW** | Uses scalar `HashMap` for cooldown tracking; no external deps beyond `serde_json` and `futures` | None needed |
| `shared_state_store.rs` | **LOW** | Pure `Arc<Mutex<HashMap>>` wrapper; no analyzer deps | None needed |

**Compile-time check:**
```bash
cargo build 2>&1 | grep 'error\[E'
```

---

## Gap 2: FEATURE-SSTP-001 — Missing EventSource::SecuritySignoff

### Problem

`SecuritySignoffAnalyzer` is fully implemented (993 lines), registered in
`mod.rs:40`, and wired in `main.rs:1304`, but the **pipeline contract does not
declare** its EventSource variant or StageContract membership:

1. `EventSource::SecuritySignoff` variant **MISSING** from enum in `pipeline_contract.rs:100-142`
2. `as_str()` arm for `"security_signoff"` **MISSING** from `pipeline_contract.rs:145-166`
3. `from_str("security_signoff")` arm **MISSING** from `pipeline_contract.rs:169-192`
4. AnomalyDetection stage `produces` set does NOT include `"security_signoff"`
5. AnomalyDetection stage `consumes` set does NOT include `"handoff_injection"` (needed by SecuritySignoffAnalyzer)

### Resolution

**Four additions to `pipeline_contract.rs`:**

1. **Add enum variant** (line 141, after `ContextPollution`):
   ```rust
   /// Security sign-off tracking outputs
   SecuritySignoff,
   ```

2. **Add `as_str()` mapping** (after line 165):
   ```rust
   Self::SecuritySignoff => "security_signoff",
   ```

3. **Add `from_str()` mapping** (after line 188):
   ```rust
   "security_signoff" => Some(Self::SecuritySignoff),
   ```

4. **Update AnomalyDetection stage contract** (lines 263-275):
   ```rust
   StageContract {
       stage: PipelineStage::AnomalyDetection,
       consumes: maplit::hashset! {
           "process".into(), "system".into(), "http_parser".into(),
           "sse_processor".into(), "handoff_injection".into(),  // ADDED
       },
       produces: maplit::hashset! {
           "multi_agent_anomaly".into(), "security_signoff".into(),  // ADDED
       },
       pass_through: true,
       stateless: false,
   },
   ```

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| New variant breaks exhaustive match elsewhere | **LOW** | No other file matches `EventSource` exhaustively | `cargo build` will catch it |
| StageContract `satisfies()` fails | **LOW** | InjectionDetection produces `handoff_injection`; chain holds | Test verifies |
| `security_signoff.rs` needs changes | **ZERO** | DDR-002 specifies declarative-only update | No code changes to analyzer |

---

## Gap 3: FEATURE-FAAP-001 — Unregistered + Unwired FileAccessAudit

### Problem

`file_access_audit.rs` (606 lines, 8 tokio tests) exists but is:
1. Commented out in `mod.rs:29`: `// pub mod file_access_audit;  // TODO(v2): fix compilation errors`
2. Wired into the `EventSource` enum already (line 138)
3. Present in Preprocessing stage `produces` set already (line 235)
4. **NOT wired** into any `AgentRunner` or `main.rs` preprocessing chain

### Resolution

**Two changes needed:**

1. **Uncomment `mod.rs` registration:**
   ```rust
   pub mod file_access_audit;
   pub use file_access_audit::FileAccessAuditAnalyzer;
   ```

2. **Wire into AgentRunner** (location TBD — `runner/agent_runner.rs` or `main.rs`):
   The analyzer must be placed **BEFORE** `SecuritySignoffAnalyzer` in the
   preprocessing chain. Approximate location:
   ```rust
   let file_access = FileAccessAuditAnalyzer::new(
       FileAccessAuditConfig::default(),
       workspace_roots,  // Vec<String> of workspace paths
   );
   runner.add_analyzer(Box::new(file_access));
   ```

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Compilation errors in `file_access_audit.rs` | **MEDIUM** | Blocked until fixed | `cargo build` immediately; fix iteratively. If >50 lines, escalate |
| Import path mismatch (`super::Analyzer` vs `crate::...`) | **LOW** | Wrong path → compile error, easy to fix | Verify `file_access_audit.rs:31` |
| AgentRunner wiring placement wrong | **LOW** | Analyzer runs but order is wrong | Place explicitly BEFORE SecuritySignoff |

---

## Cross-Cutting Issue: PipelineBuilderImpl Test Compilation

### Symptom

```rust
error[E0507]: cannot move out of `&mut` reference
   --> pipeline_orchestrator.rs:890
    |
890 | let mut orch = PipelineBuilderImpl::new()
    |                ... builder chain ...
908 |     .add_output(Box::new(MockStageAnalyzer::new("out", "output")))
    |      ^^^^^^^^^ move occurs because value does not implement `Copy`
909 |     .build()
    |      ------- value moved due to this method call
```

### Root Cause

`PipelineBuilder::add_preprocessor()` takes `&mut self` and returns `&mut Self`.
`PipelineBuilder::build()` takes `self` (by value). After chaining `&mut self`
returns, calling `.build()` tries to move ownership out of the `&mut` reference,
which is not possible without the `Copy` trait.

### Fix

Add `Clone` derive to `PipelineBuilderImpl`:

```rust
#[derive(Default, Clone)]
pub struct PipelineBuilderImpl { ... }
```

`HashMap<PipelineStage, StageAnalyzers>` implements `Clone` trivially.
`Option<Box<dyn SharedStateStore>>` — `Box<dyn SharedStateStore>` implements
`Clone` only if `SharedStateStore: Clone`, which it is not. **Alternative fix:**
Change `build()` signature to take `&mut self` and use `std::mem::take()`:

```rust
fn build(&mut self) -> Result<Box<dyn PipelineOrchestrator>, PipelineBuildError> {
    let stages = std::mem::take(&mut self.stages);
    let store = self.shared_store.take();
    // ... construct PipelineOrchestratorImpl ...
}
```

This is the recommended approach — it avoids the `Clone` constraint on
`SharedStateStore` and aligns with the builder pattern used by
`tokio::io::Builder` and similar Rust builders.

---

## Event Schema Deltas Summary

### No Schema Changes for CPDP-001

The `EventSource::ContextPollution` variant already exists. The
`ContextPollutionEvidence::finalize_alert()` method produces a JSON payload
with shape:

```json
{
  "alert_type": "context_pollution",
  "scenario_id": "agent-teams-context-pollution",
  "severity": "critical|high|medium",
  "source_agent_root_pid": 100,
  "poisoned_artifact_path": "/fixtures/team_policy.md",
  "propagation_chain": [100, 200, 300],
  "propagation_count": 3,
  "acceptance_marker": "POLICY OVERRIDE (urgent)",
  "evidence_excerpts": ["Phase I: ...", "Phase II: ...", ...],
  "phase_timestamps": {"InjectionSource": 1000, "Propagation": 2000, ...},
  "is_complete": true,
  "description": "Context pollution chain: 3 agents propagated '/fixtures/team_policy.md'"
}
```

This is additive to the existing `alert` source — existing alert consumers
ignore unknown `alert_type` values.

### No Schema Changes for FAAP-001

`EventSource::FileAccess` already exists. The analyzer emits events with the
`FileAccessAuditRecord` shape. The original `process` event passes through
unchanged. **No new enum variant needed.**

### Schema Addition for SSTP-001

One new enum variant: `EventSource::SecuritySignoff`. This is a **declarative
addition** — the analyzer already emits `source: "security_signoff"` strings.
After the change, `EventSource::from_str("security_signoff")` returns
`Some(EventSource::SecuritySignoff)`, enabling type-safe matching.

**No other file in the collector needs changes** — the string-based event
dispatch in `output.rs` and `file_logger.rs` already handles unknown source
strings gracefully.

---

## Execution Plan

### Phase A: Pipeline Builder Fix (est. 5 min)

1. Edit `pipeline_orchestrator.rs` — change `build(self)` to `build(&mut self)`
2. Use `std::mem::take()` to extract owned state
3. `cargo test pipeline_orchestrator` ✅

### Phase B: SSTP Contract Integration (est. 10 min)

1. Edit `pipeline_contract.rs` — add `EventSource::SecuritySignoff` variant
2. Add `as_str()` / `from_str()` arms
3. Update `AnomalyDetection` stage contract in `canonical_pipeline_contracts()`
4. Add `SecuritySignoff` to the roundtrip test loop
5. `cargo test pipeline_contract::tests` ✅

### Phase C: CPDP Module Activation (est. 5 min)

1. Edit `mod.rs` — uncomment 6 lines (3x `pub mod`, 3x `pub use`)
2. `cargo build` — verify compilation
3. `cargo test` — verify no regressions

### Phase D: FAAP Module Activation + Wiring (est. 15 min)

1. Edit `mod.rs` — uncomment 2 lines
2. `cargo build` — fix any compilation errors
3. Find AgentRunner wiring location
4. Wire `FileAccessAuditAnalyzer` into preprocessing chain
5. `cargo test file_access_audit::tests` ✅

### Integration Checklist

```bash
# Build
cargo build --bin mtagentlens 2>&1 | grep -c 'error'

# Lint
cargo clippy -- -D warnings 2>&1 | grep -c 'error'

# All pipeline contract tests
cargo test pipeline_contract::tests 2>&1 | tail -5

# Security signoff tests
cargo test security_signoff::tests 2>&1 | tail -5

# File access audit tests
cargo test file_access_audit::tests 2>&1 | tail -5

# Full test suite — no regressions
cargo test 2>&1 | tail -10
```

---

## Handoff to Subagent C

**Context for frontend:**
1. `EventSource::SecuritySignoff` is now registered — frontend type guard
   `isSecuritySignoffEvent()` at `security-signoff.ts:106` that checks
   `source === "security_signoff"` is correct.
2. `EventSource::FileAccess` is already registered — `isFileAccessEvent()`
   should check `source === "file_access"`. The existing `isFileOpenEvent()`
   at `file-access.ts:134` checks `source === "process"`; a new guard is
   needed for the enriched event source.
3. No other EventSource changes affect frontend contracts.

---

*Document updated by Subagent B (rust-framework-architect) for handoff to
Subagent C (frontend-visualization-expert) and Subagent D
(integration-testing-orchestrator).*
