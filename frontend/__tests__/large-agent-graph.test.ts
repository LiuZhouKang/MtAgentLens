import { describe, expect, it } from 'vitest';
import type { GraphData, ResponsibilityNode } from '@/lib/types';
import { buildLargeAgentGraph, deriveGraphMetrics } from '@/lib/large-agent-graph';

const phase = { Authoritative: { timestamp_ns: 1, overrides: null } } as const;

function agent(node_id: string): ResponsibilityNode {
  return {
    node_id,
    node_type: 'Agent',
    agent_id: node_id,
    resource: null,
    evidence_refs: [],
    timestamp_ns: 1,
    phase,
  };
}

const baseGraph: GraphData = {
  nodes: [
    agent('agent:planner'),
    agent('agent:runner'),
    {
      node_id: 'resource:file',
      node_type: 'Resource',
      agent_id: null,
      resource: { File: { canonical_path: '/workspace/app.ts' } },
      evidence_refs: ['ev:file'],
      timestamp_ns: 1,
      phase,
    },
    {
      node_id: 'boundary:system',
      node_type: 'PolicyBoundary',
      agent_id: null,
      resource: null,
      evidence_refs: [],
      timestamp_ns: 1,
      phase,
    },
  ],
  edges: [
    {
      edge_id: 'edge:base',
      edge_type: 'IntentCausesAction',
      source: 'agent:planner',
      target: 'resource:file',
      evidence_refs: ['ev:file'],
      confidence: 0.9,
      phase,
    },
  ],
};

describe('large agent graph', () => {
  it('expands a responsibility graph into a dense runtime graph for the home 3D stage', () => {
    const largeGraph = buildLargeAgentGraph(baseGraph, {
      agentSatelliteCount: 18,
      resourceSatelliteCount: 10,
    });

    expect(largeGraph.nodes.length).toBeGreaterThan(baseGraph.nodes.length * 10);
    expect(largeGraph.edges.length).toBeGreaterThan(baseGraph.edges.length * 40);
    expect(largeGraph.nodes.some((node) => node.node_id === 'agent:planner')).toBe(true);
    expect(largeGraph.nodes.some((node) => node.resource && 'LlmConversation' in node.resource)).toBe(true);
    expect(largeGraph.nodes.some((node) => node.resource && 'ToolCall' in node.resource)).toBe(true);
    expect(largeGraph.nodes.some((node) => node.resource && 'Process' in node.resource)).toBe(true);
    expect(largeGraph.edges.some((edge) => edge.edge_type === 'PolicyBoundaryCrossing')).toBe(true);
  });

  it('derives metrics from the expanded graph without relying on stale fixture counts', () => {
    const largeGraph = buildLargeAgentGraph(baseGraph, {
      agentSatelliteCount: 8,
      resourceSatelliteCount: 6,
    });
    const metrics = deriveGraphMetrics(largeGraph);

    expect(metrics.agent_count).toBe(2);
    expect(metrics.node_counts.Resource).toBeGreaterThan(baseGraph.nodes.length);
    expect(metrics.edge_counts.IntentCausesAction).toBeGreaterThan(1);
    expect(metrics.avg_confidence).toBeGreaterThan(0);
    expect(metrics.avg_confidence).toBeLessThanOrEqual(1);
  });

  it('defaults to a template-scale constellation instead of a small decorative graph', () => {
    const largeGraph = buildLargeAgentGraph(baseGraph);

    expect(largeGraph.nodes.length).toBeGreaterThan(700);
    expect(largeGraph.edges.length).toBeGreaterThan(900);
  });
});
