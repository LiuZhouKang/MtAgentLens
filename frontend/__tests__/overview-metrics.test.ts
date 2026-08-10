import { describe, expect, it } from 'vitest';
import {
  deriveClusterPressure,
  deriveEvidenceStats,
  deriveSeverityBreakdown,
  deriveTopologyTrend,
  frameTopologyCounts,
} from '@/lib/overview-metrics';
import type { ResponsibilityAlert } from '@/lib/types';

function alert(severity: ResponsibilityAlert['severity']): ResponsibilityAlert {
  return { severity } as ResponsibilityAlert;
}

describe('overview-metrics —— 真实数据派生', () => {
  it('frameTopologyCounts：live GraphSnapshot（metrics）', () => {
    const c = frameTopologyCounts({
      metrics: {
        node_counts: { Agent: 3, Resource: 2 },
        edge_counts: { IntentCausesAction: 4 },
        agent_count: 2,
      },
    });
    expect(c).toEqual({ nodes: 5, edges: 4, agents: 2 });
  });

  it('frameTopologyCounts：db GraphSnapshotFrame（graph 内嵌）', () => {
    const c = frameTopologyCounts({ graph: { nodes: [{}, {}], edges: [{}] } });
    expect(c).toEqual({ nodes: 2, edges: 1, agents: 0 });
  });

  it('deriveTopologyTrend：少于 2 帧 → 空（诚实不造曲线）', () => {
    expect(deriveTopologyTrend([{}])).toEqual([]);
    expect(deriveTopologyTrend([])).toEqual([]);
  });

  it('deriveTopologyTrend：多帧 → 按帧真实节点/边计数', () => {
    const trend = deriveTopologyTrend([
      { metrics: { node_counts: { Agent: 2 }, edge_counts: { E: 1 }, agent_count: 1 } },
      { metrics: { node_counts: { Agent: 5 }, edge_counts: { E: 4 }, agent_count: 2 } },
    ]);
    expect(trend.length).toBe(2);
    expect(trend[0]).toEqual({ name: '#1', nodes: 2, edges: 1, agents: 1 });
    expect(trend[1]).toEqual({ name: '#2', nodes: 5, edges: 4, agents: 2 });
  });

  it('deriveSeverityBreakdown：真实严重度计数', () => {
    expect(deriveSeverityBreakdown([alert('Critical'), alert('Warning'), alert('Info'), alert('Critical')]))
      .toEqual({ critical: 2, warning: 1, info: 1 });
  });

  it('deriveEvidenceStats：总数 + 按集群分布', () => {
    const stats = deriveEvidenceStats([
      { clusterId: 'cluster-a', linkedEvidence: [1, 2] },
      { clusterId: 'cluster-b', linkedEvidence: [1] },
    ]);
    expect(stats.total).toBe(3);
    expect(stats.byCluster[0]).toEqual({ name: 'cluster-a', value: 2 });
  });

  it('deriveClusterPressure：每集群边界穿越数', () => {
    const pressure = deriveClusterPressure([
      { clusterId: 'a', boundaryCrossingCount: 3 },
      { clusterId: 'b', boundaryCrossingCount: 1 },
    ]);
    expect(pressure).toEqual([
      { name: 'a', value: 3 },
      { name: 'b', value: 1 },
    ]);
  });
});
