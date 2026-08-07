import { describe, expect, it } from 'vitest';
import { pickRepresentativeFrames } from '@/lib/db-import';
import { buildFrameDiff, summarizeFrameChange } from '@/lib/snapshot-evolution';
import type { GraphMetrics, GraphSnapshotFrame } from '@/lib/types';

const metrics: GraphMetrics = { node_counts: {}, edge_counts: {}, agent_count: 0, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0 };

const frame = (id: string, nodeIds: string[], edgeKeys: string[], ts: number): GraphSnapshotFrame => ({
  snapshot_id: id,
  timestamp_ns: ts,
  graph: {
    nodes: nodeIds.map((n) => ({ node_id: n, node_type: 'Agent', agent_id: n, resource: null, evidence_refs: [], timestamp_ns: ts, phase: { Provisional: { timestamp_ns: ts } } })),
    edges: edgeKeys.map((k, i) => ({ edge_id: `e${i}`, edge_type: 'IntentCausesAction', source: k.split('>')[0], target: k.split('>')[1], evidence_refs: [], confidence: 0.8, phase: { Provisional: { timestamp_ns: ts } }, signal: null })),
  },
  metrics,
});

describe('buildFrameDiff', () => {
  it('前后帧节点/边增删正确', () => {
    const prev = frame('a', ['n1', 'n2'], ['n1>n2'], 1);
    const next = frame('b', ['n1', 'n3'], ['n1>n3', 'n1>n2'], 2);
    const diff = buildFrameDiff(prev, next);
    expect(diff.addedNodes).toEqual(['n3']);
    expect(diff.removedNodes).toEqual(['n2']);
    expect(diff.addedEdges).toEqual(['n1>n3']);
    expect(diff.removedEdges).toEqual([]);
  });

  it('空 prev 帧（图首次出现）全部为新增', () => {
    const prev = frame('a', [], [], 1);
    const next = frame('b', ['n1'], [], 2);
    const diff = buildFrameDiff(prev, next);
    expect(diff.addedNodes).toEqual(['n1']);
    expect(diff.removedNodes).toEqual([]);
  });
});

describe('summarizeFrameChange', () => {
  it('生成 delta 文案（净增量：added − removed）', () => {
    // prev 2 节点 2 边 → next 3 节点 1 边：净 +1 节点、−1 边
    const prev = frame('a', ['n1', 'n2'], ['n1>n2', 'n1>n3'], 1);
    const next = frame('b', ['n1', 'n3', 'n4'], ['n1>n3'], 2);
    expect(summarizeFrameChange(buildFrameDiff(prev, next))).toBe('+1 节点 / −1 边');
  });
});

describe('pickRepresentativeFrames（复用 db-import 导出）', () => {
  it('n ≤ max 原样返回', () => {
    const f = [1, 2, 3].map((n) => `f${n}`);
    expect(pickRepresentativeFrames(f, 30)).toEqual(f);
  });
  it('n > max 均匀降采样，含首尾', () => {
    const f = Array.from({ length: 10 }, (_, i) => `f${i + 1}`);
    expect(pickRepresentativeFrames(f, 4)).toEqual(['f1', 'f4', 'f7', 'f10']);
  });
});
