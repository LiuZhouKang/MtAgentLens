// 仪表盘真实数据派生——铁律：任何 KPI/趋势/分布由纯函数从真实数据派生；
// 算不出的诚实返回空（不造数、不用 Math.sin 模拟）。

import type { ResponsibilityAlert } from './types';

/** 任意快照帧归一化后的拓扑计数。 */
export interface TopologyCounts {
  nodes: number;
  edges: number;
  agents: number;
}

/** 归一化单帧拓扑计数（兼容 live GraphSnapshot 与 db GraphSnapshotFrame）。 */
export function frameTopologyCounts(frame: unknown): TopologyCounts {
  if (!frame || typeof frame !== 'object') return { nodes: 0, edges: 0, agents: 0 };
  const f = frame as {
    metrics?: {
      node_counts?: Record<string, number>;
      edge_counts?: Record<string, number>;
      agent_count?: number;
    };
    graph?: { nodes?: unknown[]; edges?: unknown[] };
    nodes?: unknown[];
    edges?: unknown[];
  };
  if (f.metrics) {
    return {
      nodes: Object.values(f.metrics.node_counts ?? {}).reduce((s, n) => s + (n ?? 0), 0),
      edges: Object.values(f.metrics.edge_counts ?? {}).reduce((s, n) => s + (n ?? 0), 0),
      agents: Number(f.metrics.agent_count ?? 0),
    };
  }
  const g = f.graph ?? f;
  return {
    nodes: g?.nodes?.length ?? 0,
    edges: g?.edges?.length ?? 0,
    agents: 0,
  };
}

/** 真实拓扑演化趋势：按快照帧时间序派生每帧节点/边/agent 计数。
 *  少于 2 帧返回空数组（诚实空态，不造曲线）。 */
export function deriveTopologyTrend(frames: unknown[]): Array<{ name: string; nodes: number; edges: number; agents: number }> {
  if (frames.length < 2) return [];
  return frames.map((frame, i) => {
    const c = frameTopologyCounts(frame);
    return { name: `#${i + 1}`, nodes: c.nodes, edges: c.edges, agents: c.agents };
  });
}

/** 真实告警严重度分布。 */
export function deriveSeverityBreakdown(alerts: ResponsibilityAlert[]): {
  critical: number;
  warning: number;
  info: number;
} {
  const out = { critical: 0, warning: 0, info: 0 };
  for (const a of alerts) {
    if (a.severity === 'Critical') out.critical += 1;
    else if (a.severity === 'Warning') out.warning += 1;
    else out.info += 1;
  }
  return out;
}

/** 真实证据统计：总证据数 + 按集群分布（top 8）。 */
export function deriveEvidenceStats(
  clusterModels: Array<{ clusterId: string; linkedEvidence: unknown[] }>,
): { total: number; byCluster: Array<{ name: string; value: number }> } {
  const total = clusterModels.reduce((s, m) => s + m.linkedEvidence.length, 0);
  const byCluster = clusterModels.slice(0, 8).map((m) => ({
    name: m.clusterId.length > 12 ? `${m.clusterId.slice(0, 12)}…` : m.clusterId,
    value: m.linkedEvidence.length,
  }));
  return { total, byCluster };
}

/** 真实集群压力：每集群边界穿越数（top 6），用于集群压力条。 */
export function deriveClusterPressure(
  clusterModels: Array<{ clusterId: string; boundaryCrossingCount: number }>,
): Array<{ name: string; value: number }> {
  return clusterModels.slice(0, 6).map((m) => ({ name: m.clusterId, value: m.boundaryCrossingCount }));
}
