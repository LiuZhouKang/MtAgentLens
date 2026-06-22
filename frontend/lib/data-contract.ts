// Data Contract v2: Timeline + ProcessTree + AnomalyStream
// Aligns with collector/src/server/data_contract.rs
//
// Cross-reference design:
//   TimelineStep ──(pid)──> ProcessNode
//   TimelineStep ──(anomalyRefs)──> AnomalyStreamEvent
//   AnomalyStreamEvent ──(linkedTimelineSteps)──> TimelineStep
//   AnomalyStreamEvent ──(linkedProcessPids)──> ProcessNode
//
// Stream version carried per-event for gradual rollout.
// Sequence number enables dedup / gap detection on the SSE client.

// ═══════════════════════════════════════════════════════════════════════════
// SCHEMA VERSION CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

export const SCHEMA_VERSIONS = {
  timeline: '2.0.0',
  process_tree: '2.0.0',
  anomaly_stream: '2.0.0',
  unified_stream: '2.0.0',
} as const;

// ═══════════════════════════════════════════════════════════════════════════
// TIMELINE CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

/** Full timeline snapshot for a single agent. */
export interface TimelineContract {
  timeline_id: string;
  agent_id: string;
  start_time_ns: number;
  end_time_ns: number;
  steps: TimelineStep[];
  total_duration_ms: number;
  /** v2: summary metrics for dashboard without walking all steps. */
  metrics?: TimelineMetrics;
}

export interface TimelineMetrics {
  step_count: number;
  file_ops: number;
  network_ops: number;
  exec_ops: number;
  delegation_ops: number;
  max_gap_ms: number;
  anomaly_linked_count: number;
}

/**
 * Event type discriminator for timeline steps.
 * v1: FileRead | FileWrite | NetworkConnect | ProcessExec | Delegation
 * v2 additions: FileDelete | NetworkSend | NetworkRecv | CredentialAccess | ContextInjection
 */
export type TimelineEventType =
  | 'FileRead'
  | 'FileWrite'
  | 'FileDelete'
  | 'NetworkConnect'
  | 'NetworkSend'
  | 'NetworkRecv'
  | 'ProcessExec'
  | 'Delegation'
  | 'CredentialAccess'
  | 'ContextInjection';

/** Single step in a timeline. */
export interface TimelineStep {
  step_id: string;
  timestamp_ns: number;
  /** Human-readable event category. */
  event_type: TimelineEventType;
  description: string;
  /** Canonical path, host:port, or other resource identifier. */
  resource: string | null;
  agent_id: string | null;
  source_event_id: string | null;
  evidence_refs: string[];
  operation: string | null;

  // ── v2 fields ──
  /** Monotonic index within this timeline (0-based). */
  step_index: number;
  /** Nanoseconds since previous step; null for the first step. */
  duration_since_prev_ns: number | null;
  /** PID that generated this event — links to ProcessNode. */
  pid: number | null;
  /** Anomaly IDs that reference this step — links to AnomalyStreamEvent. */
  anomaly_refs: string[];
  /** Extensible metadata bag (e.g. file size, HTTP status code). */
  metadata: Record<string, unknown>;
}

// ═══════════════════════════════════════════════════════════════════════════
// PROCESS TREE CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

/** Full process tree snapshot. */
export interface ProcessTreeContract {
  tree_id: string;
  root_pid: number;
  root_comm: string;
  snapshot_time_ns: number;
  nodes: ProcessNode[];
  edges: ProcessEdge[];
  /** v2: summary stats to avoid full-tree traversal on the client. */
  metrics?: ProcessTreeMetrics;
}

export interface ProcessTreeMetrics {
  total_nodes: number;
  total_edges: number;
  active_nodes: number;
  exited_nodes: number;
  max_depth: number;
  agent_count: number;
  anomaly_linked_count: number;
}

export interface ProcessNode {
  node_id: string;
  pid: number;
  ppid: number;
  comm: string;
  start_time_ns: number;
  end_time_ns: number | null;
  exit_code: number | null;
  resource_usage: ProcessResourceUsage;

  // ── v2 fields ──
  /** Agent identity resolved for this process. */
  agent_id: string | null;
  /** Number of anomalies linked to this process. */
  anomaly_count: number;
  /** Whether any linked anomalies are still active. */
  has_active_anomalies: boolean;
  /** User-defined labels for grouping/filtering (e.g. "worker", "browser"). */
  labels: string[];
}

export interface ProcessResourceUsage {
  cpu_time_us: number;
  memory_peak_kb: number;
  file_ops_count: number;
  network_ops_count: number;
}

export type ProcessEdgeType = 'fork' | 'exec' | 'thread';

export interface ProcessEdge {
  edge_id: string;
  parent_pid: number;
  child_pid: number;
  edge_type: ProcessEdgeType;
  timestamp_ns: number;

  // ── v2 fields ──
  /** The agent that triggered this spawn. */
  agent_id: string | null;
}

/**
 * Incremental update to the process tree.
 *
 * node_added    — new process appeared; `node` is populated.
 * node_removed  — process exited; `node` has end_time_ns/exit_code.
 * node_updated  — existing node changed (e.g. resource usage grew);
 *                 `node` is current state, `previous_node` is prior state.
 * edge_added    — new parent→child relationship; `edge` is populated.
 * edge_removed  — relationship ended; `edge` is populated.
 */
export type ProcessTreeUpdateType =
  | 'node_added'
  | 'node_removed'
  | 'node_updated'
  | 'edge_added'
  | 'edge_removed';

export interface ProcessTreeUpdate {
  tree_id: string;
  update_type: ProcessTreeUpdateType;
  timestamp_ns: number;
  node: ProcessNode | null;
  edge: ProcessEdge | null;

  // ── v2 fields ──
  /** Previous node state (for node_updated — enables diff rendering). */
  previous_node: ProcessNode | null;
  /** Monotonic sequence within this tree's update stream. */
  update_index: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// ANOMALY STREAM CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

export type AnomalySeverity = 'info' | 'warning' | 'critical';

/** Lifecycle status for an anomaly. */
export type AnomalyStatus = 'active' | 'resolved' | 'dismissed';

/**
 * Anomaly type discriminator.
 * PolicyViolation      — rule/policy engine flagged a violation.
 * UnexpectedBehavior   — pattern matcher found anomalous behavior.
 * ResourceExhaustion   — resource limits exceeded.
 * DelegationAnomaly    — handoff/delegation pattern flagged.
 * ContextPollution     — taint propagated across trust boundary.
 */
export type AnomalyType =
  | 'PolicyViolation'
  | 'UnexpectedBehavior'
  | 'ResourceExhaustion'
  | 'DelegationAnomaly'
  | 'ContextPollution';

export interface AnomalyStreamEvent {
  anomaly_id: string;
  timestamp_ns: number;
  agent_id: string;
  anomaly_type: AnomalyType;
  severity: AnomalySeverity;
  /** 0.0 – 1.0 */
  confidence: number;
  classification: AnomalyClassification;
  affected_resources: AffectedResource[];
  evidence_chain: string[];
  remediation_hint: string | null;

  // ── v2 fields ──
  /** Lifecycle status. */
  status: AnomalyStatus;
  /** When the anomaly was resolved/dismissed. */
  resolved_at_ns: number | null;
  /** Timeline step IDs this anomaly references — cross-stream link. */
  linked_timeline_steps: string[];
  /** Process PIDs involved — cross-stream link. */
  linked_process_pids: number[];
  /** LLM observer verdict (if observer is enabled). */
  observer_verdict: ObserverVerdict | null;
  /** Extensible metadata. */
  metadata: Record<string, unknown>;
}

export interface AnomalyClassification {
  primary_class: string;
  sub_class: string | null;
  rule_ids: string[];
  pattern_ids: string[];
  score_breakdown: Record<string, number>;
}

export type ResourceType = 'File' | 'Network' | 'Process' | 'Credential' | 'Context';

export type ResourceOperation = 'read' | 'write' | 'connect' | 'execute' | 'delete' | 'inject';

export interface AffectedResource {
  resource_type: ResourceType;
  resource_id: string;
  operation: ResourceOperation;
}

/** LLM observer judgment for an anomaly. */
export interface ObserverVerdict {
  decision: 'Safe' | 'Unsafe' | 'Uncertain';
  confidence: number;
  reasoning: string;
  attacked_flag: boolean;
  /** "Once" | "Task" | "All" */
  cache_ttl: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// UNIFIED SSE STREAM CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

/** Batch marker for multi-message delivery of a single logical snapshot. */
export interface BatchMarker {
  batch_id: string;
  batch_index: number;
  batch_total: number;
}

/**
 * Discriminated union of the three stream event types.
 * Serialized with `stream_type` tag, matching the Rust `#[serde(tag = "stream_type")]`.
 */
export type UnifiedStreamEvent =
  | { stream_type: 'timeline' } & TimelineStep
  | { stream_type: 'process_tree' } & ProcessTreeUpdate
  | { stream_type: 'anomaly' } & AnomalyStreamEvent;

/**
 * v2 envelope: carries stream_version and sequence_number at the top level.
 * SSE consumers read `stream_type` to route, then extract the typed payload.
 */
export interface StreamEnvelope {
  /** "2.0.0" — all events in a run share the same version. */
  stream_version: string;
  /** Monotonically increasing across all stream types in this run. */
  sequence_number: number;
  /** The run this event belongs to. */
  run_id: string;
  /** Wall-clock epoch milliseconds when this envelope was emitted. */
  emitted_at_ms: number;
  /** Non-null when this event is part of a multi-message batch. */
  batch: BatchMarker | null;
  /** The wrapped stream event. */
  stream_type: 'timeline' | 'process_tree' | 'anomaly';
}

/** StreamEnvelope + the flattened event fields — what actually arrives on the wire. */
export type StreamEnvelopeWithEvent = StreamEnvelope & (
  | ({ stream_type: 'timeline' } & TimelineStep)
  | ({ stream_type: 'process_tree' } & ProcessTreeUpdate)
  | ({ stream_type: 'anomaly' } & AnomalyStreamEvent)
);

// ═══════════════════════════════════════════════════════════════════════════
// SNAPSHOT RESPONSE (REST endpoint: GET /api/runs/:id/snapshot)
// ═══════════════════════════════════════════════════════════════════════════

/** Full snapshot combining all three streams at a point in time. */
export interface RunSnapshot {
  run_id: string;
  snapshot_time_ns: number;
  stream_version: string;
  timeline: TimelineContract | null;
  process_tree: ProcessTreeContract | null;
  anomalies: AnomalyStreamEvent[];
  /** Last seen sequence_number — clients resume SSE from here. */
  last_sequence_number: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// TYPE GUARDS
// ═══════════════════════════════════════════════════════════════════════════

export function isTimelineStep(
  event: UnifiedStreamEvent,
): event is { stream_type: 'timeline' } & TimelineStep {
  return event.stream_type === 'timeline';
}

export function isProcessTreeUpdate(
  event: UnifiedStreamEvent,
): event is { stream_type: 'process_tree' } & ProcessTreeUpdate {
  return event.stream_type === 'process_tree';
}

export function isAnomalyStreamEvent(
  event: UnifiedStreamEvent,
): event is { stream_type: 'anomaly' } & AnomalyStreamEvent {
  return event.stream_type === 'anomaly';
}

/** Extract the stream_type from a raw SSE message (before full parse). */
export function peekStreamType(raw: string): UnifiedStreamEvent['stream_type'] | null {
  try {
    const parsed = JSON.parse(raw) as { stream_type?: string };
    if (
      parsed.stream_type === 'timeline' ||
      parsed.stream_type === 'process_tree' ||
      parsed.stream_type === 'anomaly'
    ) {
      return parsed.stream_type;
    }
    return null;
  } catch {
    return null;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// STREAM UTILITIES
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Detect gaps in a sequence-numbered stream.
 * Returns indices where `seq[i] - seq[i-1] > 1`.
 */
export function detectSequenceGaps(events: { sequence_number: number }[]): number[] {
  const gaps: number[] = [];
  for (let i = 1; i < events.length; i++) {
    if (events[i].sequence_number - events[i - 1].sequence_number > 1) {
      gaps.push(i);
    }
  }
  return gaps;
}

/**
 * Merge a ProcessTreeUpdate into a local ProcessTreeContract.
 * Returns a new tree with the update applied; immutable.
 */
export function applyProcessTreeUpdate(
  tree: ProcessTreeContract,
  update: ProcessTreeUpdate,
): ProcessTreeContract {
  // Ensure tree_id matches
  if (tree.tree_id !== update.tree_id) return tree;

  const nodes = [...tree.nodes];
  const edges = [...tree.edges];
  const now = update.timestamp_ns;

  switch (update.update_type) {
    case 'node_added': {
      if (update.node && !nodes.find((n) => n.node_id === update.node!.node_id)) {
        nodes.push(update.node);
      }
      break;
    }
    case 'node_removed': {
      if (update.node) {
        const idx = nodes.findIndex((n) => n.node_id === update.node!.node_id);
        if (idx >= 0) {
          nodes[idx] = { ...nodes[idx], end_time_ns: now, exit_code: update.node.exit_code };
        }
      }
      break;
    }
    case 'node_updated': {
      if (update.node) {
        const idx = nodes.findIndex((n) => n.node_id === update.node!.node_id);
        if (idx >= 0) nodes[idx] = update.node;
      }
      break;
    }
    case 'edge_added': {
      if (update.edge && !edges.find((e) => e.edge_id === update.edge!.edge_id)) {
        edges.push(update.edge);
      }
      break;
    }
    case 'edge_removed': {
      if (update.edge) {
        const idx = edges.findIndex((e) => e.edge_id === update.edge!.edge_id);
        if (idx >= 0) edges.splice(idx, 1);
      }
      break;
    }
  }

  return {
    ...tree,
    snapshot_time_ns: now,
    nodes,
    edges,
  };
}

/**
 * Build a lookup map: pid → ProcessNode (latest by node_id if multiple).
 */
export function buildProcessIndex(
  nodes: ProcessNode[],
): Map<number, ProcessNode> {
  const map = new Map<number, ProcessNode>();
  // Reverse iteration so earlier entries are overwritten by later ones.
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (!map.has(n.pid)) map.set(n.pid, n);
  }
  return map;
}

/**
 * Enrich a TimelineStep with the process comm name from the process tree.
 * Returns a new step with `metadata.comm` set.
 */
export function enrichStepWithProcess(
  step: TimelineStep,
  processIndex: Map<number, ProcessNode>,
): TimelineStep {
  if (step.pid == null) return step;
  const node = processIndex.get(step.pid);
  if (!node) return step;
  return {
    ...step,
    metadata: { ...step.metadata, comm: node.comm },
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFAULT FACTORIES (for tests and initialization)
// ═══════════════════════════════════════════════════════════════════════════

export function createEmptyTimelineStep(overrides?: Partial<TimelineStep>): TimelineStep {
  return {
    step_id: '',
    timestamp_ns: 0,
    event_type: 'FileRead',
    description: '',
    resource: null,
    agent_id: null,
    source_event_id: null,
    evidence_refs: [],
    operation: null,
    step_index: 0,
    duration_since_prev_ns: null,
    pid: null,
    anomaly_refs: [],
    metadata: {},
    ...overrides,
  };
}

export function createEmptyProcessNode(overrides?: Partial<ProcessNode>): ProcessNode {
  return {
    node_id: '',
    pid: 0,
    ppid: 0,
    comm: '',
    start_time_ns: 0,
    end_time_ns: null,
    exit_code: null,
    resource_usage: { cpu_time_us: 0, memory_peak_kb: 0, file_ops_count: 0, network_ops_count: 0 },
    agent_id: null,
    anomaly_count: 0,
    has_active_anomalies: false,
    labels: [],
    ...overrides,
  };
}

export function createEmptyAnomalyEvent(
  overrides?: Partial<AnomalyStreamEvent>,
): AnomalyStreamEvent {
  return {
    anomaly_id: '',
    timestamp_ns: 0,
    agent_id: '',
    anomaly_type: 'PolicyViolation',
    severity: 'info',
    confidence: 0,
    classification: {
      primary_class: '',
      sub_class: null,
      rule_ids: [],
      pattern_ids: [],
      score_breakdown: {},
    },
    affected_resources: [],
    evidence_chain: [],
    remediation_hint: null,
    status: 'active',
    resolved_at_ns: null,
    linked_timeline_steps: [],
    linked_process_pids: [],
    observer_verdict: null,
    metadata: {},
    ...overrides,
  };
}
