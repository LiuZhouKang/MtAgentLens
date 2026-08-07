import { describe, expect, test } from 'vitest';
import { parseSnapshotJson, SnapshotParseError } from '@/lib/snapshot-import';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';

function makeSnapshotJson(): string {
  return JSON.stringify({
    snapshot_id: 'test-1',
    graph: {
      nodes: [
        { node_id: 'n1', node_type: 'Agent', agent_id: 'a1', resource: null, evidence_refs: [], timestamp_ns: 0, phase: 'Provisional' },
        { node_id: 'n2', node_type: 'Resource', agent_id: null, resource: null, evidence_refs: [], timestamp_ns: 0, phase: 'Provisional' },
        { node_id: 'n3', node_type: 'Agent', agent_id: 'a2', resource: null, evidence_refs: [], timestamp_ns: 0, phase: 'Provisional' },
      ],
      edges: [
        { edge_id: 'e1', edge_type: 'IntentCausesAction', source: 'n1', target: 'n2', evidence_refs: [], confidence: 0.9, phase: 'Provisional' },
      ],
    },
    metrics: {
      node_counts: { Agent: 2, Resource: 1 },
      edge_counts: { IntentCausesAction: 1 },
      agent_count: 2,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 0,
      avg_confidence: 0.9,
    },
    alerts: [
      {
        alert_id: 'al-1',
        timestamp_ns: 1000,
        title: '测试告警',
        severity: 'Critical',
        decision: 'Unsafe',
        confidence: 0.85,
        authority: 'Authoritative',
        reasoning: 'r',
        summary: 's',
        action_hint: 'h',
        display_profile: 'UnsafeByEngine',
        primary_agent: { agent_id: 'a1', confidence: 0.9, role: null },
        involved_agents: [],
        rule_verdicts: [],
        pattern_verdicts: [],
        evidence: [],
        behavior_timeline: [],
        engine: { rule_verdicts: [], pattern_verdicts: [] },
      },
    ],
  } as unknown as { graph: GraphData; alerts: ResponsibilityAlert[] });
}

describe('parseSnapshotJson — local data snapshot import', () => {
  test('parses a valid snapshot with graph, metrics and alerts', () => {
    const result = parseSnapshotJson(makeSnapshotJson(), 'run-42.json');

    expect(result.label).toBe('run-42.json');
    expect(result.id).toBe('file:run-42.json');
    expect(result.nodeCount).toBe(3);
    expect(result.edgeCount).toBe(1);
    expect(result.alertCount).toBe(1);
    expect(result.metrics.node_counts.Agent).toBe(2);
    expect(result.graph.nodes.length).toBe(3);
    expect(result.alerts[0].title).toBe('测试告警');
  });

  test('derives metrics from the graph when the snapshot omits them', () => {
    const json = makeSnapshotJson();
    const withoutMetrics = JSON.parse(json) as Record<string, unknown>;
    delete withoutMetrics.metrics;

    const result = parseSnapshotJson(JSON.stringify(withoutMetrics), 'bare.json');
    expect(result.metrics.agent_count).toBe(2);
    expect(result.metrics.node_counts.Resource).toBe(1);
    expect(result.metrics.cross_agent_edges).toBe(0);
  });

  test('accepts a snapshot with zero alerts', () => {
    const json = makeSnapshotJson();
    const withEmptyAlerts = JSON.parse(json) as Record<string, unknown>;
    withEmptyAlerts.alerts = [];

    const result = parseSnapshotJson(JSON.stringify(withEmptyAlerts), 'quiet.json');
    expect(result.alertCount).toBe(0);
    expect(result.alerts).toEqual([]);
  });

  test('rejects invalid JSON with a clear error', () => {
    expect(() => parseSnapshotJson('{not json', 'broken.json')).toThrow(SnapshotParseError);
    expect(() => parseSnapshotJson('{not json', 'broken.json')).toThrow(/JSON/);
  });

  test('rejects an empty file', () => {
    expect(() => parseSnapshotJson('', 'empty.json')).toThrow(/空/);
  });

  test('rejects a snapshot without a graph node list', () => {
    const json = makeSnapshotJson();
    const bad = JSON.parse(json) as Record<string, unknown>;
    delete bad.graph;

    expect(() => parseSnapshotJson(JSON.stringify(bad), 'nog.json')).toThrow(/graph\.nodes/);
  });

  test('rejects a snapshot whose graph.nodes is not an array', () => {
    const json = makeSnapshotJson();
    const bad = JSON.parse(json) as Record<string, unknown>;
    (bad.graph as Record<string, unknown>).nodes = 'nope';

    expect(() => parseSnapshotJson(JSON.stringify(bad), 'bad.json')).toThrow(/graph\.nodes/);
  });

  test('rejects a snapshot without an alerts array', () => {
    const json = makeSnapshotJson();
    const bad = JSON.parse(json) as Record<string, unknown>;
    delete bad.alerts;

    expect(() => parseSnapshotJson(JSON.stringify(bad), 'noalerts.json')).toThrow(/alerts/);
  });
});
