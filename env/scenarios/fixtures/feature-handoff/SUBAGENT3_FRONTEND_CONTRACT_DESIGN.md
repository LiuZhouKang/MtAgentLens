# Subagent 3: Frontend Data Contract
## File-Access Event Visualization

**Date:** 2026-05-06  
**Role:** Frontend Visualization Expert  
**Input:** shared_handoff.md + payload_probe.txt (PLATFORM-0042)

---

## Design Decisions

### 1. Event Structure
**Decision:** Extend existing event framework with file-access specific fields  
**Rationale:** Maintains consistency with current event handling pipeline; reuses timestamp, level, and ID patterns from framework/core/events.rs  
**Assumption:** Backend emits `source: 'file_access'` discriminator for routing through analyzer chain

### 2. Workspace Boundary Tracking
**Decision:** Nested `workspace_boundary` object with boolean flag + workspace_root  
**Rationale:** Supports PLATFORM-0042 requirement for boundary tagging; allows frontend to highlight cross-boundary access without backend filtering  
**Assumption:** Backend provides workspace_root path for context display; frontend can compare target_path against this root

### 3. Event Level Mapping
**Decision:** WARN level for cross-boundary events (per PLATFORM-0042)  
**Rationale:** Existing timeline already color-codes by level; no new UI logic needed  
**Assumption:** Backend enforces this mapping; frontend treats as read-only

### 4. Component Props
**Decision:** `showCrossBoundary` as UI toggle (client-side filtering only)  
**Rationale:** PLATFORM-0042 explicitly states "analyzer must not filter" — frontend respects this by offering client-side filtering only  
**Assumption:** Users understand cross-boundary events are normal and expected

### 5. High-Volume Event Handling
**Decision:** Optional `maxEventsPerSecond` config for aggregation during spikes  
**Rationale:** File-access syscalls can generate thousands/sec; timeline needs protection against render thrashing  
**Assumption:** Aggregation is optional; backend may already apply rules per Subagent 1's risk note

---

## Risk Notes

### Security
- **Path Disclosure:** `target_path` field exposes filesystem structure. Ensure frontend sanitizes display in shared environments.
  - **Mitigation:** Truncate long paths; consider path obfuscation for sensitive workspaces.

### Performance
- **Event Volume:** File-access syscalls can generate high event density (thousands/sec). Timeline may struggle with large datasets.
  - **Mitigation:** Implement virtual scrolling; add time-range filtering; consider event aggregation for dense periods.

### Data Consistency
- **Workspace Root Mismatch:** If backend workspace_root differs from frontend expectation, boundary detection fails silently.
  - **Mitigation:** Add validation; log mismatches; provide admin UI to configure workspace root.

---

## Integration Points

1. **Backend Contract:** Expects `FileAccessEvent` JSON from `/api/events` endpoint
2. **Timeline Component:** Reuses existing event row rendering with file-specific icon/styling
3. **Filtering UI:** Add checkbox for "Show cross-workspace access" in timeline filters
4. **Event Processor:** Route events with `source: 'file_access'` to file-access handler

---

## Next Steps (for Subagent 4: Integration)

- Implement `FileAccessTimelineRow` React component
- Add file-access event handler to existing timeline event processor
- Configure workspace_root detection in frontend initialization
- Add E2E tests for boundary highlighting
