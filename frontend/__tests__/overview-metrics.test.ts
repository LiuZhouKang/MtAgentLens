import { describe, expect, it } from 'vitest';
import {
  deriveResponsibilityUnitPressure,
  deriveEvidenceMatchDistribution,
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

  it('deriveEvidenceMatchDistribution：完整 L1→L5 全谱（缺失等级如实计 0，绝不臆造）', () => {
    const stats = deriveEvidenceMatchDistribution([
      { match_level: 'L1Exact' },
      { match_level: 'L1Exact' },
      { match_level: 'L3Containment' },
      { match_level: 'L5Substring' },
    ]);
    // 全量 4 条；始终返回 5 级全谱，L2/L4 如实为 0（数据缺失≠编造）
    expect(stats.total).toBe(4);
    expect(stats.byLevel.map((l) => l.name)).toEqual([
      'L1Exact', 'L2Normalized', 'L3Containment', 'L4Basename', 'L5Substring',
    ]);
    expect(stats.byLevel).toEqual([
      { name: 'L1Exact', short: 'L1', value: 2, percent: 50 },
      { name: 'L2Normalized', short: 'L2', value: 0, percent: 0 },
      { name: 'L3Containment', short: 'L3', value: 1, percent: 25 },
      { name: 'L4Basename', short: 'L4', value: 0, percent: 0 },
      { name: 'L5Substring', short: 'L5', value: 1, percent: 25 },
    ]);
  });

  it('deriveResponsibilityUnitPressure：每责任单元边界穿越数', () => {
    const pressure = deriveResponsibilityUnitPressure([
      { unitId: 'a', boundaryCrossingCount: 3 },
      { unitId: 'b', boundaryCrossingCount: 1 },
    ]);
    expect(pressure).toEqual([
      { name: 'a', value: 3 },
      { name: 'b', value: 1 },
    ]);
  });
});
