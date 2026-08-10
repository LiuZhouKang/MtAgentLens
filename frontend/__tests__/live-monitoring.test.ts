import { describe, expect, it } from 'vitest';
import { apiSnapshotsToMetricExtract, deriveLiveMonitoring } from '@/lib/live/live-monitoring';
import type { ApiNonLinkDiagnostic, GraphSnapshot } from '@/lib/types';

function snap(id: string, nodeCount: number): GraphSnapshot {
  return {
    snapshot_id: id,
    timestamp_ns: 1,
    nodes: [],
    edges: [],
    metrics: {
      node_counts: { Agent: nodeCount },
      edge_counts: { IntentCausesAction: 2 },
      agent_count: 1,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 1,
      avg_confidence: 0.8,
    },
  };
}

describe('live-monitoring —— 直播运行监控派生', () => {
  it('apiSnapshotsToMetricExtract 映射 API 快照 → 度量帧', () => {
    const ex = apiSnapshotsToMetricExtract([snap('s1', 3)]);
    expect(ex.available).toBe(true);
    expect(ex.frames[0].snapshot_id).toBe('s1');
    expect(ex.frames[0].nodeCount).toBe(3);
    expect(ex.frames[0].edgeCount).toBe(2);
    expect(ex.frames[0].taintChains).toBe(1);
    expect(ex.frames[0].avgConfidence).toBe(0.8);
  });

  it('空快照 → available=false', () => {
    expect(apiSnapshotsToMetricExtract([]).available).toBe(false);
  });

  it('deriveLiveMonitoring 产出 6 视角；API 拿不到的诚实 unavailable', () => {
    const mon = deriveLiveMonitoring({
      snapshots: [snap('s1', 3)],
      alerts: [],
      intents: [],
      actions: [],
      nonLinks: [
        { reason: 'intent_no_match', match_level_considered: 'L5Substring' } as ApiNonLinkDiagnostic,
      ],
    });
    expect(mon.sourceLabel).toBe('实时运行');
    // 有快照 → 拓扑漂移可见
    expect(mon.topologyDrift?.latest?.nodeCount).toBe(3);
    expect(mon.topologyDrift).not.toBeNull();
    // non_links → 失配可见
    expect(mon.intentMismatch?.diagnosticsCount).toBe(1);
    expect(mon.intentMismatch?.evidenceMatch.weak).toBe(1);
    // 无 API 来源视角 → 诚实 null（缺表视角 UI 空态）
    expect(mon.policyCompliance).toBeNull();
    expect(mon.networkSessions).toBeNull();
    // toolFootprint 恒有（intents/actions 计数）
    expect(mon.toolFootprint).not.toBeNull();
  });
});
