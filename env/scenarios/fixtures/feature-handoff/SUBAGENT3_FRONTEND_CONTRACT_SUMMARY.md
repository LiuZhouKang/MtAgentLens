# Frontend Data Contract Summary
## File-Access Event Visualization

### TypeScript Event Interface

```typescript
interface FileAccessEvent {
  // Standard framework fields
  id: string;
  timestamp: number;
  level: 'INFO' | 'WARN' | 'ERROR';
  source: 'file_access';
  
  // File-access syscall data
  syscall: 'open' | 'openat';
  target_path: string;
  flags: number;
  mode: number;
  pid: number;
  comm: string;
  
  // PLATFORM-0042: Workspace boundary
  workspace_boundary: {
    is_inside: boolean;
    workspace_root: string;
  };
}
```

### Component Props

```typescript
interface FileAccessTimelineRowProps {
  event: FileAccessEvent;
  isHighlighted: boolean;
  onSelect: (event: FileAccessEvent) => void;
}

interface FileAccessFilterProps {
  showCrossBoundary: boolean;
  onToggleCrossBoundary: (show: boolean) => void;
  workspaceRoot?: string;
}
```

### Key Decisions

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Event Structure | Extend framework | Consistency with existing pipeline |
| Boundary Tracking | Nested object | Supports PLATFORM-0042 requirement |
| Level Mapping | WARN for cross-boundary | Reuses existing color-coding |
| Filtering | Client-side only | Respects "no backend filtering" constraint |
| High Volume | Optional aggregation | Protects timeline from render thrashing |

### Critical Assumptions

1. Backend emits `source: 'file_access'` discriminator
2. Backend provides `workspace_root` for boundary detection
3. Backend enforces WARN level for cross-boundary events
4. Frontend can safely truncate paths for display

### Risk Mitigations

- **Path Disclosure:** Truncate long paths; consider obfuscation
- **Event Volume:** Virtual scrolling + time-range filtering
- **Data Consistency:** Validate workspace_root; log mismatches
