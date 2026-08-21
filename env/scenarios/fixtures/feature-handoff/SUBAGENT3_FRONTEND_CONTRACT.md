# Subagent 3: Frontend Data Contract
## Observability Pipeline v2 — Complete Handoff Visualization

**Date:** 2026-05-23
**Role:** Frontend Visualization Expert
**Input:** shared_handoff.md + payload_probe.txt (PLATFORM-0042)

---

## Coverage Map: Handoff Extension → Frontend Types

| # | Handoff Extension | Frontend Type | Discriminator |
|---|---|---|---|
| 1 | File-access ↔ agent correlation | `FileAccessCorrelationEdge` | `'file_correlation'` |
| 2 | Workspace boundary anomaly alerts | `WorkspaceBoundaryAlert` | `'workspace_boundary'` |
| 3 | Handoff lifecycle latency breakdowns | `HandoffLifecycleTiming` | `'handoff_lifecycle'` |
| 4 | Chain depth tagging | `chain_depth: number` on `FileAccessEvent` | (field) |
| 5 | Handoff-completion records | `HandoffCompletionRecord` | `'handoff_completion'` |
| 6 | Context contradiction alerts | `ContextContradictionAlert` | `'context_contradiction'` |
| 7 | Context-window saturation warnings | `ContextSaturationWarning` | `'context_saturation'` |
| 8 | Policy-driven tool-gating feed | `ToolGateDecision` | `'tool_gate'` |
| 9 | Delegation tree visualization | `DelegationTree` + `DelegationNode` | `'delegation_tree'` |
| 10 | Inter-agent dependency edges + orphaned work | `InterAgentDependencyEdge` + `OrphanedWorkAlert` | `'agent_dependency'` / `'orphaned_work'` |
| 11 | Prompt-injection drift detection | `PromptDriftAlert` | `'prompt_drift'` |
| 12 | Subagent idle wall-clock attribution | `SubagentIdleTimeBreakdown` | `'subagent_idle'` |

---

## Design Decisions

### 1. Event Source Allocation

**Decision:** Single `source: 'handoff_observability'` with `data.sub_type` discriminator, except file-access correlation which attaches to existing `file_access` events.

**Rationale:** Minimizes source proliferation. The existing dispatcher pattern maps `source` → handler; a single source lets us batch-handle all handoff events in one normaliser while the `sub_type` field drives row-level rendering.

**Assumption:** Backend emits handoff events under `source: 'handoff_observability'`.

### 2. Delegation Tree as Time-Indexed View Model

**Decision:** `DelegationTree` is a client-side view model derived from `HandoffCompletionRecord` (spawn/complete pairs) accumulated over time. Each `DelegationNode` carries `spawned_at` and `completed_at` timestamps for time-slider replay.

**Rationale:** Handoff 9 requires time-indexed tree visualization. Building a reactive tree from spawn/complete events lets operators replay topology at any point — they slide to time T and see only nodes that existed at T.

**Assumption:** Backend emits `spawn` records with `parent_agent_id`; frontend can reconstruct hierarchy without server-side graph API.

### 3. Alert Severity Mapping

**Decision:** Alert types that are direct security/integrity issues (`context_contradiction`) map to `'error'` severity. Resource/monitoring issues (`context_saturation`) map to `'warning'`. This dovetails with existing `FlatAnomalyAlert.severity` coloring.

**Rationale:** Existing timeline already color-codes by severity. No new UI logic needed; operators can filter the alert stream by severity as they do today.

**Assumption:** Backend enforces this mapping; frontend treats as read-only.

### 4. Tool-Gate Audit Feed as Append-Only Security Feed

**Decision:** `ToolGateDecision` records carry immutable fields (timestamp, policy_rule_id, tool_name, agent_id, denied_reason). The frontend renders these as a filtered security feed, not as general timeline events.

**Rationale:** Handoff 8 explicitly calls for an "auditable security feed." Operators need to distinguish policy enforcement (correct blocking) from injection attempts being blocked. A dedicated filter tab serves this need without cluttering the main timeline.

**Assumption:** The existing `ToolGateDecision` records are never mutated post-emission.

### 5. Workspace Boundary Alerts Build on PLATFORM-0042

**Decision:** `WorkspaceBoundaryAlert` extends the `workspace_boundary` tag from PLATFORM-0042 with an alert wrapper. When a file-access event crosses a boundary AND the accessing agent is a subagent with a declared workspace, emit a `WorkspaceBoundaryAlert`.

**Rationale:** PLATFORM-0042 covers the boundary tag. Handoff 2 adds the alert layer on top — subagent-specific boundary violations are a subset of cross-boundary events. This decision avoids duplicating the boundary detection logic.

**Assumption:** Backend provides both `workspace_boundary.is_inside` (from PLATFORM-0042) and the subagent's `declared_workspace` for alert enrichment.

### 6. Cost Attribution via HandoffCompletionRecord

**Decision:** `HandoffCompletionRecord` carries `bytes_in`, `bytes_out`, `token_count` fields. The frontend computes per-node cost from a configurable `cost_per_1k_tokens` rate and renders it as a tooltip/badge on `DelegationNode`.

**Rationale:** Handoff 5 requires "post-hoc cost attribution per subagent node." Keeping cost computation client-side lets operators adjust rates without backend changes.

**Assumption:** Token count is available from backend; pricing tier is frontend-configurable.

### 7. Inter-Agent Dependency Edges as Graph Overlay (Handoff #10)

**Decision:** `InterAgentDependencyEdge` records are accumulated client-side and rendered as overlay edges on the `DelegationTree` visualization. `OrphanedWorkAlert` fires when no consumer reads a subagent's output within the run window.

**Rationale:** Handoff 10 requires both dependency tracking and orphan detection. Rendering edges as a graph overlay on the delegation tree lets operators see both hierarchy (parent→child) and data flow (producer→consumer) in one view.

**Assumption:** Backend emits dependency edges whenever an agent reads a file/tool_result/handoff_summary produced by another agent.

### 8. Drift Score as Configurable Threshold (Handoff #11)

**Decision:** `PromptDriftAlert` carries a `drift_score` (0.0–1.0) and a categorical `drift_category`. The frontend renders drift alerts in the alert stream; operators can set a `driftScoreThreshold` in `HandoffVisualizationConfig` to control which alerts surface (default: 0.3 → `scope_shift` and above).

**Rationale:** Semantic drift is a spectrum. Surfacing every minor rewording (drift < 0.1) would flood the alert stream. A configurable threshold lets operators tune sensitivity per deployment.

**Assumption:** Backend provides the embedding-based drift score; frontend thresholds are for display filtering only.

### 9. Idle-Time Breakdown as Companion to Lifecycle Timing (Handoff #12)

**Decision:** `SubagentIdleTimeBreakdown` is a separate type from `HandoffLifecycleTiming`, rendered as a companion stacked bar below the active lifecycle bar. The `lifecycle_event_id` foreign key links them. The `dominant_idle_phase` field highlights the biggest bottleneck.

**Rationale:** Handoff 3 covers active phases (dispatch, execute) while Handoff 12 covers idle/waiting phases (queued, queueing). Keeping them as separate types avoids conflating active work with waiting time and lets operators toggle each independently.

**Assumption:** Backend can distinguish model-inference-queueing from tool-call-roundtrip latency.

---

## Component Props Summary

| Component | Props Interface | Purpose |
|-----------|----------------|---------|
| `FileAccessTimelineRow` | `FileAccessTimelineRowProps` | Timeline row for file-access events with correlation edges |
| `HandoffLifecycleRow` | `HandoffLifecycleRowProps` | Timeline row showing active phase latencies |
| `SubagentIdleTimeRow` | `SubagentIdleTimeRowProps` | Timeline row showing idle/waiting phase breakdown |
| `WorkspaceBoundaryAlertRow` | `WorkspaceBoundaryAlertRowProps` | Alert row for boundary violations |
| `ContextContradictionAlertRow` | `ContextContradictionAlertRowProps` | ERROR-level alert for directive conflicts |
| `ContextSaturationWarningRow` | `ContextSaturationWarningRowProps` | WARN-level saturation indicator |
| `ToolGateDecisionRow` | `ToolGateDecisionRowProps` | Security-feed row for denied tools |
| `OrphanedWorkAlertRow` | `OrphanedWorkAlertRowProps` | Alert for subagent output never consumed |
| `PromptDriftAlertRow` | `PromptDriftAlertRowProps` | Alert for semantic drift from root task |
| `DelegationTreeView` | `DelegationTreeViewProps` | Time-indexed tree visualization with dependency overlay |
| `DelegationNodeDetail` | `DelegationNodeDetailProps` | Per-subagent detail panel (all alerts, timings, edges) |
| `HandoffFilterBar` | `HandoffFilterBarProps` | Unified filter controls for all 12 handoff event types |

---

## Risk Notes

### Security
- **Path Disclosure in WorkspaceBoundaryAlert:** `accessed_path` and `declared_workspace` reveal filesystem structure. Truncate in shared dashboards.
- **Policy Rule Exposure:** `ToolGateDecision.policy_rule_id` leaks internal policy structure. Consider mapping to human-readable labels.

### Performance
- **Delegation Tree Rendering:** Large delegation trees (100+ nodes) may strain React reconciliation. Use `reactflow` or a canvas-based renderer for >50 nodes.
- **Tool-Gate Event Volume:** High-churn subagents may emit thousands of `ToolGateDecision` records. Paginate the security feed; aggregate by `policy_rule_id` for summary view.

### Data Consistency
- **Chain Depth Accuracy:** `chain_depth` depends on correct parent-child tracking in backend. If backend loses a spawn event, depth is wrong for the entire subtree.
- **Contradiction Detection Completeness:** False negatives in context contradiction detection mean conflicts go unreported. Frontend cannot detect this — it relies on backend classifier accuracy.
- **Orphan Detection Timing:** Orphaned work alerts depend on a run-idle timeout — if the parent agent reads the subagent's output after the timeout fires, a false-positive orphan alert results.
- **Drift Embedding Stability:** Drift scores depend on consistent embedding models between backend runs. Model version changes can shift all scores, requiring threshold recalibration.

---

## Integration Points

1. **Event Dispatcher:** Add `'handoff_observability'` case to `event-dispatcher.ts`, routing to `normaliseHandoffEvent()`.
2. **DashboardEvent Union:** Add 11 new variants to the `DashboardEvent` type (up from 7).
3. **Timeline Panel:** Register row renderers for each new `type` discriminator (12 row types total).
4. **Security Feed Tab:** Add dedicated "Tool Gates" tab in the filter bar alongside existing "Alerts" tab.
5. **Delegation Tree Panel:** New panel component with dependency-edge overlay, sibling to `TimelinePanel` and `ProcessTreeView`.
6. **Idle-Time Companion View:** Render `SubagentIdleTimeBreakdown` as a stacked bar below `HandoffLifecycleTiming` in the per-subagent detail panel.
7. **Drift Score Threshold Config:** Expose `driftScoreThreshold` in the `HandoffVisualizationConfig` for operator tuning.
8. **API Client:** No new endpoints needed — all data arrives via existing `/api/events` SSE stream.
