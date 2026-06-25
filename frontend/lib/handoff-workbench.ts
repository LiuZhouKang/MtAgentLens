import type {
  DelegationNode,
  HandoffObsMetrics,
  HandoffObservabilityEvent,
} from './handoff-observability-contract';

export type HandoffSignalBucket = 'boundary' | 'orchestration' | 'debris' | 'integrity';

export interface HandoffFocusEvent {
  id: string;
  title: string;
  bucket: HandoffSignalBucket;
  severity: 'info' | 'warning' | 'critical';
  agentId: string | null;
  detail: string;
}

export interface HandoffAgentLane {
  nodeId: string;
  agentId: string;
  depth: number;
  state: 'active' | 'completed';
  childCount: number;
  contextOverheadRatio: number;
  modelMismatch: boolean;
  pressureScore: number;
}

export interface HandoffWorkbenchModel {
  activeLanes: HandoffAgentLane[];
  focusEvents: HandoffFocusEvent[];
  signalCounts: Record<HandoffSignalBucket, number>;
  leadLane: HandoffAgentLane | null;
  leadFocus: HandoffFocusEvent | null;
}

function severityRank(severity: HandoffFocusEvent['severity']) {
  if (severity === 'critical') return 2;
  if (severity === 'warning') return 1;
  return 0;
}

function bucketRank(bucket: HandoffSignalBucket) {
  switch (bucket) {
    case 'boundary':
      return 0;
    case 'orchestration':
      return 1;
    case 'debris':
      return 2;
    case 'integrity':
      return 3;
    default:
      return 4;
  }
}

function eventPriority(event: HandoffObservabilityEvent) {
  switch (event.sub_type) {
    case 'DATA_LEAK':
      return 0;
    case 'BOUNDARY_DRIFT':
      return 1;
    case 'FANOUT_STORM':
      return 2;
    case 'CONTEXT_SATURATION':
      return 3;
    case 'ORPHANED_WORK':
      return 4;
    case 'PIPELINE_INTEGRITY':
      return 5;
    case 'CORRECTIVE_LOOP':
      return 6;
    case 'CONTEXT_CONFLICT':
      return 7;
    case 'STRATEGY_DIVERGENCE':
      return 8;
    default:
      return 9;
  }
}

function eventTimestampMs(event: HandoffObservabilityEvent) {
  return (
    (event as { timestamp_ms?: number }).timestamp_ms ??
    (event as { dispatched_at_ms?: number }).dispatched_at_ms ??
    0
  );
}

function flattenTree(root: DelegationNode | null): DelegationNode[] {
  if (!root) return [];

  const output: DelegationNode[] = [];
  const visit = (node: DelegationNode) => {
    output.push(node);
    const children = ((node as DelegationNode & { children?: DelegationNode[] }).children ?? []);
    children.forEach(visit);
  };

  visit(root);
  return output;
}

function classifyBucket(event: HandoffObservabilityEvent): HandoffSignalBucket {
  switch (event.sub_type) {
    case 'BOUNDARY_TAG':
    case 'BOUNDARY_DRIFT':
    case 'DATA_LEAK':
      return 'boundary';
    case 'HANDOFF_SPAWN':
    case 'HANDOFF_COMPLETE':
    case 'FANOUT_STORM':
    case 'CONTEXT_SATURATION':
    case 'CONTEXT_CONFLICT':
    case 'CORRECTIVE_LOOP':
    case 'STRATEGY_DIVERGENCE':
      return 'orchestration';
    case 'ORPHANED_WORK':
      return 'debris';
    case 'PIPELINE_INTEGRITY':
    case 'DRIFT_ALERT':
    case 'TOOL_DENIED':
    case 'FILE_CORRELATION':
      return 'integrity';
    default:
      return 'integrity';
  }
}

export function inferHandoffEventSeverity(event: HandoffObservabilityEvent): HandoffFocusEvent['severity'] {
  const rawSeverity = String((event as { severity?: string }).severity ?? '').toLowerCase();
  if (rawSeverity === 'critical' || rawSeverity === 'error') return 'critical';
  if (rawSeverity === 'warning' || rawSeverity === 'warn') return 'warning';

  if (event.sub_type === 'FANOUT_STORM' || event.sub_type === 'CORRECTIVE_LOOP' || event.sub_type === 'DATA_LEAK') {
    return 'critical';
  }
  if (event.sub_type === 'CONTEXT_SATURATION' || event.sub_type === 'ORPHANED_WORK' || event.sub_type === 'PIPELINE_INTEGRITY') {
    return 'warning';
  }

  return 'info';
}

function describeEvent(event: HandoffObservabilityEvent): HandoffFocusEvent {
  switch (event.sub_type) {
    case 'CONTEXT_SATURATION':
      return {
        id: event.spawn_id,
        title: `${event.agent_id} 上下文接近饱和`,
        bucket: 'orchestration',
        severity: inferHandoffEventSeverity(event),
        agentId: event.agent_id,
        detail: `${(event.overhead_ratio * 100).toFixed(0)}% overhead / ${Math.round(event.injected_bytes / 1024)}KB injected`,
      };
    case 'ORPHANED_WORK':
      return {
        id: event.alert_id,
        title: `${event.producer_agent_id} 输出已脱离消费链`,
        bucket: 'debris',
        severity: inferHandoffEventSeverity(event),
        agentId: event.producer_agent_id,
        detail: event.artifact_path,
      };
    case 'BOUNDARY_DRIFT':
      return {
        id: event.alert_id,
        title: `${event.agent_id} 边界漂移高于同层中位数`,
        bucket: 'boundary',
        severity: inferHandoffEventSeverity(event),
        agentId: event.agent_id,
        detail: `${event.cross_boundary_ratio.toFixed(2)} cross-boundary ratio`,
      };
    case 'PIPELINE_INTEGRITY':
      return {
        id: event.alert_id,
        title: 'workspace_tag 存活率出现衰减',
        bucket: 'integrity',
        severity: inferHandoffEventSeverity(event),
        agentId: null,
        detail: `${(event.tag_survival_rate * 100).toFixed(1)}% survival`,
      };
    case 'FANOUT_STORM':
      return {
        id: event.alert_id,
        title: '委托扇出速度明显偏离完成速度',
        bucket: 'orchestration',
        severity: inferHandoffEventSeverity(event),
        agentId: null,
        detail: `${event.ratio.toFixed(2)}:1 within ${event.window_seconds}s`,
      };
    case 'DATA_LEAK':
      return {
        id: event.alert_id,
        title: `${event.leaking_agent_id} 与 ${event.accessing_agent_id} 之间出现内容碰撞`,
        bucket: 'boundary',
        severity: inferHandoffEventSeverity(event),
        agentId: event.leaking_agent_id,
        detail: event.exposed_path,
      };
    default: {
      const agentId =
        (event as { agent_id?: string }).agent_id ??
        (event as { child_agent_id?: string }).child_agent_id ??
        (event as { parent_agent_id?: string }).parent_agent_id ??
        null;
      return {
        id:
          (event as { alert_id?: string }).alert_id ??
          (event as { spawn_id?: string }).spawn_id ??
          `${event.sub_type}-${agentId ?? 'global'}`,
        title: event.sub_type,
        bucket: classifyBucket(event),
        severity: inferHandoffEventSeverity(event),
        agentId,
        detail: agentId ?? 'global signal',
      };
    }
  }
}

export function buildHandoffWorkbenchModel(
  tree: DelegationNode | null,
  metrics: HandoffObsMetrics,
  events: HandoffObservabilityEvent[],
): HandoffWorkbenchModel {
  const nodes = flattenTree(tree);
  const activeLanes = nodes
    .map((node) => {
      const childCount = node.child_ids.length;
      const isActive = node.completed_at_ms == null;
      const modelMismatch = !!node.model_assigned && !!node.model_optimal && node.model_assigned !== node.model_optimal;
      const pressureScore =
        (isActive ? 26 : 10) +
        node.chain_depth * 10 +
        childCount * 12 +
        node.context_overhead_ratio * 34 +
        (modelMismatch ? 14 : 0);

      return {
        nodeId: node.node_id,
        agentId: node.agent_id,
        depth: node.chain_depth,
        state: isActive ? 'active' : 'completed',
        childCount,
        contextOverheadRatio: node.context_overhead_ratio,
        modelMismatch,
        pressureScore,
      } satisfies HandoffAgentLane;
    })
    .sort((left, right) => right.pressureScore - left.pressureScore);

  const focusEvents = events
    .map((event) => ({ event, focus: describeEvent(event) }))
    .sort((left, right) => {
      const severityDelta = severityRank(right.focus.severity) - severityRank(left.focus.severity);
      if (severityDelta !== 0) return severityDelta;
      const bucketDelta = bucketRank(left.focus.bucket) - bucketRank(right.focus.bucket);
      if (bucketDelta !== 0) return bucketDelta;
      const priorityDelta = eventPriority(left.event) - eventPriority(right.event);
      if (priorityDelta !== 0) return priorityDelta;
      const timestampDelta = eventTimestampMs(right.event) - eventTimestampMs(left.event);
      if (timestampDelta !== 0) return timestampDelta;
      return left.focus.id.localeCompare(right.focus.id);
    });

  const signalCounts: Record<HandoffSignalBucket, number> = {
    boundary: 0,
    orchestration: 0,
    debris: metrics.orphaned_artifacts,
    integrity: 0,
  };

  focusEvents.forEach(({ focus: event }) => {
    signalCounts[event.bucket] += 1;
  });

  return {
    activeLanes,
    focusEvents: focusEvents.map(({ focus }) => focus),
    signalCounts,
    leadLane: activeLanes[0] ?? null,
    leadFocus: focusEvents[0]?.focus ?? null,
  };
}
