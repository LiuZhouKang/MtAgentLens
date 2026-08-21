// Handoff Observability Pipeline v2 — Frontend Data Contract
// Subagent 3: Frontend Visualization Expert — 2026-05-23
//
// Covers all 12 handoff agent observability extensions from shared_handoff.md
// + PLATFORM-0042 workspace boundary requirements.

// ============================================================================
// 1. FILE-ACCESS (with chain-depth extension — Handoff #4)
// ============================================================================

/** Workspace boundary classification per PLATFORM-0042 */
interface WorkspaceBoundary {
  /** True if target_path is inside project workspace */
  is_inside: boolean;
  /** Project workspace root path for context display */
  workspace_root: string;
}

/** File-access event from eBPF syscall tracking.
 *  Extended with chain_depth per Handoff #4. */
interface FileAccessEvent {
  // Standard event fields (inherited from framework)
  id: string;
  timestamp: number;       // Unix milliseconds
  level: 'INFO' | 'WARN' | 'ERROR';
  source: 'file_access';

  // File-access specific fields
  syscall: 'open' | 'openat';
  target_path: string;
  flags: number;
  mode: number;
  pid: number;
  comm: string;

  // PLATFORM-0042: Workspace boundary tracking
  workspace_boundary: WorkspaceBoundary;

  // Handoff #4: Originating handoff chain depth
  // N=0 for root agent, N+1 for each nested subagent spawn
  chain_depth: number;
}

// ============================================================================
// 2. FILE-ACCESS ↔ AGENT CORRELATION (Handoff #1)
// ============================================================================

/** Links a file-access event to the agent tool-call that triggered it.
 *  Rendered as an edge in the responsibility graph. */
interface FileAccessCorrelationEdge {
  id: string;
  timestamp: number;
  type: 'file_correlation';
  file_access_event_id: string;
  agent_id: string;           // Subagent identity
  tool_call_id: string;       // Tool invocation that caused the file access
  tool_name: string;          // e.g. "Read", "Write", "Edit"
  target_path: string;
  chain_depth: number;        // Handoff #4
}

// ============================================================================
// 3. WORKSPACE BOUNDARY ANOMALY ALERTS (Handoff #2)
// ============================================================================

/** Alert emitted when a handoff agent accesses paths outside its
 *  declared workspace boundary or reads sensitive config files. */
interface WorkspaceBoundaryAlert {
  id: string;
  timestamp: number;
  type: 'workspace_boundary';
  severity: 'WARN' | 'ERROR';       // ERROR for sensitive config access
  agent_id: string;
  chain_depth: number;
  accessed_path: string;
  declared_workspace: string;
  violation_type: 'outside_workspace' | 'sensitive_config' | 'outside_project_root';
  file_access_event_id: string;      // Backlink to triggering FileAccessEvent
}

// ============================================================================
// 4. HANDOFF LIFECYCLE LATENCY BREAKDOWN (Handoff #3)
// ============================================================================

/** Latency breakdown for each handoff lifecycle phase.
 *  Rendered as a stacked-horizontal-bar in the timeline row. */
interface HandoffLifecycleTiming {
  id: string;
  timestamp: number;          // Start of dispatch phase
  type: 'handoff_lifecycle';
  agent_id: string;
  parent_agent_id: string;
  chain_depth: number;

  // Phase durations in milliseconds
  phases: {
    dispatch_ms: number;      // Parent → handoff request sent
    context_inject_ms: number;// System prompt + handoff content injected
    execute_ms: number;       // Subagent reasoning + tool calls
    collect_ms: number;       // Results collected back by parent
  };
  total_ms: number;
  status: 'running' | 'completed' | 'timeout' | 'error';
}

// ============================================================================
// 5. HANDOFF-COMPLETION RECORDS (Handoff #5)
// ============================================================================

/** Structured handoff-completion record with context sizes and token counts.
 *  Enables post-hoc cost attribution per subagent node. */
interface HandoffCompletionRecord {
  id: string;
  timestamp: number;          // Completion time
  type: 'handoff_completion';
  agent_id: string;
  parent_agent_id: string;
  chain_depth: number;

  // Context metrics
  bytes_in: number;           // Context injected into subagent
  bytes_out: number;          // Results collected from subagent
  token_count: number;        // Final-result token count
  context_budget_total: number; // Subagent's total context window (bytes)

  // Attribution
  model_id: string;           // e.g. "claude-sonnet-4-6"
  duration_ms: number;        // Total wall-clock duration

  // Tree construction
  spawn_event_id: string;     // Links to the spawn event for tree building
}

// ============================================================================
// 6. CONTEXT CONTRADICTION ALERTS (Handoff #6)
// ============================================================================

/** ERROR-level alert when a subagent receives instructions that directly
 *  conflict with the root agent's original directive. */
interface ContextContradictionAlert {
  id: string;
  timestamp: number;
  type: 'context_contradiction';
  severity: 'ERROR';
  agent_id: string;               // Subagent that received conflicting instructions
  parent_agent_id: string;        // Agent that injected the conflicting handoff
  root_agent_id: string;          // Root agent whose directive was contradicted

  // Contradiction details
  root_directive_summary: string;   // Excerpt of root agent's original directive
  conflicting_instruction: string;   // The injected instruction that conflicts
  contradiction_type: 'directive_override' | 'scope_broadening' | 'constraint_removal' | 'goal_reversal';
  confidence: number;              // 0.0–1.0 classifier confidence
}

// ============================================================================
// 7. CONTEXT-WINDOW SATURATION WARNINGS (Handoff #7)
// ============================================================================

/** WARNING when handoff content exceeds 60% of subagent's context budget,
 *  indicating insufficient room for effective reasoning. */
interface ContextSaturationWarning {
  id: string;
  timestamp: number;
  type: 'context_saturation';
  severity: 'WARN';
  agent_id: string;
  chain_depth: number;

  // Saturation metrics
  injected_content_bytes: number;  // Size of handoff content injected
  context_budget_bytes: number;    // Subagent's total context window
  saturation_ratio: number;        // injected / budget (0.0–1.0)
  overhead_category: 'warning' | 'critical'; // warning: >60%, critical: >85%

  // Context breakdown
  system_prompt_bytes: number;
  handoff_content_bytes: number;
  conversation_history_bytes: number;
}

// ============================================================================
// 8. POLICY-DRIVEN TOOL-GATING AUDIT FEED (Handoff #8)
// ============================================================================

/** Auditable record of every denied tool invocation.
 *  Rendered in a dedicated security feed tab, not the main timeline. */
interface ToolGateDecision {
  id: string;
  timestamp: number;
  type: 'tool_gate';
  severity: 'INFO';               // Denials are expected during normal operation

  // Identity
  agent_id: string;
  chain_depth: number;

  // Decision details
  policy_rule_id: string;         // Enforcing policy rule identifier
  tool_name: string;              // Denied tool name (e.g. "Bash", "Write")
  tool_args_summary: string;      // Truncated args for audit context
  denied_reason: string;          // Human-readable reason from policy engine

  // Classification
  decision_type: 'legitimate_enforcement' | 'potential_injection_blocked';
}

// ============================================================================
// 9. DELEGATION TREE VISUALIZATION (Handoff #9)
// ============================================================================

/** A single node in the delegation tree. Built client-side from
 *  HandoffCompletionRecord spawn/complete pairs. */
interface DelegationNode {
  id: string;                    // agent_id
  parent_id: string | null;      // null for root agent
  chain_depth: number;

  // Temporal bounds for time-slider replay
  spawned_at: number;            // Unix ms
  completed_at: number | null;   // null if still running

  // Aggregate stats (accumulated from HandoffCompletionRecord)
  total_duration_ms: number;
  total_tokens: number;
  bytes_in: number;
  bytes_out: number;
  child_count: number;

  // Alert summary for badges on tree nodes
  alert_counts: {
    workspace_boundary: number;
    context_contradiction: number;
    context_saturation: number;
    tool_gate_denials: number;
    orphaned_work: number;
    prompt_drift: number;
  };

  // Visualization state (managed by tree renderer)
  layout?: {
    x: number;
    y: number;
    collapsed: boolean;
  };
}

/** Time-indexed delegation tree view model.
 *  Replayable: operators can slide to time T and see the tree at that point. */
interface DelegationTree {
  run_id: string;
  nodes: Map<string, DelegationNode>;
  root_id: string;
  generated_at: number;          // Timestamp of last tree update

  // Time-slider bounds
  time_range: {
    earliest_spawn: number;
    latest_completion: number | null; // null if run still active
  };

  // Summary stats
  total_subagents: number;
  max_chain_depth: number;
  active_subagents: number;
}

// ============================================================================
// 10. INTER-AGENT DEPENDENCY EDGES (Handoff #10)
// ============================================================================

/** Tracks which subagent output artifacts are consumed by downstream sibling
 *  or parent agents. Built from file-access and handoff-completion records. */
interface InterAgentDependencyEdge {
  id: string;
  timestamp: number;
  type: 'agent_dependency';
  producer_agent_id: string;       // Subagent that produced the artifact
  consumer_agent_id: string;       // Agent that consumed the artifact
  artifact_type: 'file' | 'tool_result' | 'handoff_summary';
  artifact_path_or_id: string;     // File path, tool call ID, or handoff record ID
  consumed_at: number;             // Unix ms when consumer read the artifact
  chain_depth_delta: number;       // consumer_depth - producer_depth (neg = upward flow)
}

/** Alert emitted when a subagent's output is never read back by any other agent.
 *  Flags wasted compute and potential handoff gaps. */
interface OrphanedWorkAlert {
  id: string;
  timestamp: number;               // When orphan detection fired (after run idle timeout)
  type: 'orphaned_work';
  severity: 'WARN';
  agent_id: string;                // Subagent whose output was orphaned
  chain_depth: number;
  orphaned_artifact_type: 'file' | 'tool_result' | 'handoff_summary';
  orphaned_artifact_path_or_id: string;
  estimated_tokens_wasted: number; // Tokens consumed by the orphaned subagent
  estimated_cost_wasted: number;   // Computed from token count × rate
  parent_agent_id: string;         // Agent that should have consumed this
}

// ============================================================================
// 11. PROMPT-INJECTION DRIFT DETECTION (Handoff #11)
// ============================================================================

/** Alert emitted when semantic embedding of a subagent's received handoff prompt
 *  diverges significantly from the root agent's original task. */
interface PromptDriftAlert {
  id: string;
  timestamp: number;
  type: 'prompt_drift';
  severity: 'INFO' | 'WARN' | 'ERROR';  // Severity maps to drift score thresholds
  agent_id: string;                      // Subagent whose prompt drifted
  root_agent_id: string;                 // Root agent for baseline comparison
  chain_depth: number;
  drift_score: number;                   // 0.0 (identical) – 1.0 (completely unrelated)
  drift_category: 'none' | 'minor_rewording' | 'scope_shift' | 'goal_divergence' | 'contradiction';
  root_task_summary: string;             // Excerpt of root agent's original directive
  received_prompt_summary: string;       // Excerpt of subagent's received handoff
  re_dispatch_recommended: boolean;      // True when drift exceeds operator threshold
}

// ============================================================================
// 12. SUBAGENT IDLE WALL-CLOCK ATTRIBUTION (Handoff #12)
// ============================================================================

/** Stacked latency breakdown for subagent idle/waiting time.
 *  Complements HandoffLifecycleTiming (active phases) with idle-phase attribution.
 *  Rendered as a stacked-horizontal-bar distinct from the active lifecycle bar. */
interface SubagentIdleTimeBreakdown {
  id: string;
  timestamp: number;
  type: 'subagent_idle';
  agent_id: string;
  chain_depth: number;
  lifecycle_event_id: string;            // Links to parent HandoffLifecycleTiming

  // Idle phase durations in milliseconds
  idle_phases: {
    queued_awaiting_dispatch_ms: number;    // Waiting in parent's dispatch queue
    context_injection_io_ms: number;        // I/O time to inject handoff content
    model_inference_queueing_ms: number;    // Waiting for model inference slot
    tool_call_roundtrip_ms: number;         // Network round-trip for tool calls
    result_collection_io_ms: number;        // I/O to collect results back
  };
  total_idle_ms: number;
  total_wall_clock_ms: number;             // Total wall clock for cross-reference
  idle_ratio: number;                      // total_idle / total_wall_clock
  dominant_idle_phase: keyof SubagentIdleTimeBreakdown['idle_phases'];
}

// ============================================================================
// 13. COMPONENT PROPS INTERFACES
// ============================================================================

// --- Timeline Row Props ---

interface FileAccessTimelineRowProps {
  event: FileAccessEvent;
  isHighlighted: boolean;
  correlationEdges: FileAccessCorrelationEdge[];  // Handoff #1: linked correlations
  onSelect: (event: FileAccessEvent) => void;
  onAgentClick: (agentId: string) => void;        // Navigate to agent detail
}

interface HandoffLifecycleRowProps {
  lifecycle: HandoffLifecycleTiming;
  isHighlighted: boolean;
  onSelect: (lifecycle: HandoffLifecycleTiming) => void;
  onAgentClick: (agentId: string) => void;
}

interface WorkspaceBoundaryAlertRowProps {
  alert: WorkspaceBoundaryAlert;
  isHighlighted: boolean;
  onSelect: (alert: WorkspaceBoundaryAlert) => void;
  onPathClick: (path: string) => void;            // Filter timeline by affected path
}

interface ContextContradictionAlertRowProps {
  alert: ContextContradictionAlert;
  isHighlighted: boolean;
  onSelect: (alert: ContextContradictionAlert) => void;
}

interface ContextSaturationWarningRowProps {
  warning: ContextSaturationWarning;
  isHighlighted: boolean;
  onSelect: (warning: ContextSaturationWarning) => void;
}

interface ToolGateDecisionRowProps {
  decision: ToolGateDecision;
  isHighlighted: boolean;
  onSelect: (decision: ToolGateDecision) => void;
}

// --- Handoff #10–#12 Row Props ---

interface OrphanedWorkAlertRowProps {
  alert: OrphanedWorkAlert;
  isHighlighted: boolean;
  onSelect: (alert: OrphanedWorkAlert) => void;
  onAgentClick: (agentId: string) => void;
}

interface PromptDriftAlertRowProps {
  alert: PromptDriftAlert;
  isHighlighted: boolean;
  onSelect: (alert: PromptDriftAlert) => void;
  onAgentClick: (agentId: string) => void;
  onReDispatchRequest: (agentId: string) => void;  // Trigger re-dispatch action
}

interface SubagentIdleTimeRowProps {
  idleTime: SubagentIdleTimeBreakdown;
  isHighlighted: boolean;
  onSelect: (idleTime: SubagentIdleTimeBreakdown) => void;
  onAgentClick: (agentId: string) => void;
}

// --- Panel Props ---

interface DelegationTreeViewProps {
  tree: DelegationTree;
  currentTime: number;                // Time-slider position (Unix ms)
  selectedNodeId: string | null;
  onNodeSelect: (nodeId: string) => void;
  onTimeChange: (time: number) => void;
  layoutMode: 'tree' | 'radial' | 'force'; // Handoff #9: replayable topology
}

interface DelegationNodeDetailProps {
  node: DelegationNode;
  completionRecords: HandoffCompletionRecord[];
  lifecycleTimings: HandoffLifecycleTiming[];    // All lifecycle phases for this node
  idleTimeBreakdowns: SubagentIdleTimeBreakdown[]; // Handoff #12
  boundaryAlerts: WorkspaceBoundaryAlert[];
  contradictionAlerts: ContextContradictionAlert[];
  saturationWarnings: ContextSaturationWarning[];
  toolGateDecisions: ToolGateDecision[];
  orphanedWorkAlerts: OrphanedWorkAlert[];       // Handoff #10
  promptDriftAlerts: PromptDriftAlert[];          // Handoff #11
  dependencyEdges: InterAgentDependencyEdge[];    // Handoff #10
  onClose: () => void;
}

// --- Filter Props ---

interface HandoffFilterBarProps {
  // Visibility toggles (client-side filtering only — Handoff #2 constraint)
  showCrossBoundary: boolean;
  showWorkspaceAlerts: boolean;
  showLifecycleTimings: boolean;
  showIdleTimeBreakdowns: boolean;
  showContextContradictions: boolean;
  showContextSaturation: boolean;
  showToolGateDecisions: boolean;
  showOrphanedWork: boolean;
  showPromptDrift: boolean;
  showDependencyEdges: boolean;

  // Callbacks
  onToggleCrossBoundary: (show: boolean) => void;
  onToggleWorkspaceAlerts: (show: boolean) => void;
  onToggleLifecycleTimings: (show: boolean) => void;
  onToggleIdleTimeBreakdowns: (show: boolean) => void;
  onToggleContextContradictions: (show: boolean) => void;
  onToggleContextSaturation: (show: boolean) => void;
  onToggleToolGateDecisions: (show: boolean) => void;
  onToggleOrphanedWork: (show: boolean) => void;
  onTogglePromptDrift: (show: boolean) => void;
  onToggleDependencyEdges: (show: boolean) => void;

  // Config
  workspaceRoot?: string;
  chainDepthFilter?: number;          // Show only nodes at this depth or shallower
  driftScoreThreshold?: number;       // Minimum drift score to surface (Handoff #11)
}

// ============================================================================
// 11. DASHBOARD EVENT UNION EXTENSION
// ============================================================================

/** New variants to add to the existing DashboardEvent union type.
 *  Each maps to a source='handoff_observability' event with a specific sub_type. */
type HandoffDashboardEvent =
  | FileAccessCorrelationEdge    // type: 'file_correlation'
  | WorkspaceBoundaryAlert       // type: 'workspace_boundary'
  | HandoffLifecycleTiming       // type: 'handoff_lifecycle'
  | HandoffCompletionRecord      // type: 'handoff_completion'
  | ContextContradictionAlert    // type: 'context_contradiction'
  | ContextSaturationWarning     // type: 'context_saturation'
  | ToolGateDecision             // type: 'tool_gate'
  | InterAgentDependencyEdge     // type: 'agent_dependency'
  | OrphanedWorkAlert            // type: 'orphaned_work'
  | PromptDriftAlert             // type: 'prompt_drift'
  | SubagentIdleTimeBreakdown;   // type: 'subagent_idle'

// ============================================================================
// 12. EVENT NORMALISER SIGNATURE
// ============================================================================

/** Normaliser for handoff observability events.
 *  Follows existing pattern: raw Event → Flat* type */
type HandoffEventNormaliser = (
  raw: Event & { data: HandoffObservabilityPayload },
  index: number
) => HandoffDashboardEvent;

/** Wire format expected from /api/events under source='handoff_observability' */
interface HandoffObservabilityPayload {
  sub_type:
    | 'file_correlation'
    | 'workspace_boundary_alert'
    | 'handoff_lifecycle'
    | 'handoff_completion'
    | 'context_contradiction'
    | 'context_saturation'
    | 'tool_gate_decision'
    | 'agent_dependency'
    | 'orphaned_work_alert'
    | 'prompt_drift_alert'
    | 'subagent_idle_time';
  // ... domain-specific fields per sub_type
  agent_id: string;
  chain_depth: number;
  timestamp: number;
}

/** Configuration for handoff visualization behavior */
interface HandoffVisualizationConfig {
  costPer1kTokens: number;            // For cost attribution badges (Handoff #5)
  saturationWarningThreshold: number; // Default 0.60 (>60% = WARN per Handoff #7)
  saturationCriticalThreshold: number;// Default 0.85
  pathTruncationLength: number;       // For WorkspaceBoundaryAlert path display
  maxTreeNodesForReactFlow: number;   // Switch to canvas renderer above this threshold
  treeLayoutMode: 'tree' | 'radial' | 'force';
}
