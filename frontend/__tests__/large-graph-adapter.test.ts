import { describe, expect, it } from 'vitest';
import { adaptGraphToLargeGraph } from '@/lib/large-graph-adapter';
import { demoGraph } from '@/lib/demo-data';
import type { GraphData } from '@/lib/types';

describe('large-graph-adapter', () => {
  it('adapts GraphData into ForceGraph nodes/links and assigns clusters', () => {
    const adapted = adaptGraphToLargeGraph(demoGraph);
    expect(adapted.nodes.length).toBeLessThanOrEqual(demoGraph.nodes.length);
    expect(adapted.links.length).toBeLessThanOrEqual(demoGraph.edges.length);

    const node = adapted.nodes[0];
    expect(typeof node.id).toBe('string');
    expect(typeof node.node_type).toBe('string');
    expect(typeof node.agent_id).toBe('string');
    expect(node.cluster_id.startsWith('cluster-')).toBe(true);
    expect(typeof node.description).toBe('string');
  });

  it('applies pinned positions as fx/fy/fz', () => {
    const pinned = {
      [demoGraph.nodes[0].node_id]: { x: 1, y: 2, z: 3 },
    };
    const adapted = adaptGraphToLargeGraph(demoGraph, pinned);
    const node = adapted.nodes.find((n) => n.id === demoGraph.nodes[0].node_id);
    expect(node?.fx).toBe(1);
    expect(node?.fy).toBe(2);
    expect(node?.fz).toBe(3);
  });

  it('merges policy boundary nodes by resource identity', () => {
    const graph: GraphData = {
      nodes: [
        {
          node_id: 'agent-a',
          node_type: 'Agent',
          agent_id: 'agent-a',
          resource: null,
          evidence_refs: [],
          timestamp_ns: 0,
          phase: { Provisional: { timestamp_ns: 0 } },
        },
        {
          node_id: 'b1',
          node_type: 'PolicyBoundary',
          agent_id: null,
          resource: { ToolCall: { tool_name: 'UserPrompt', call_id: 'c1' } },
          evidence_refs: [],
          timestamp_ns: 0,
          phase: { Provisional: { timestamp_ns: 0 } },
        },
        {
          node_id: 'b2',
          node_type: 'PolicyBoundary',
          agent_id: null,
          resource: { ToolCall: { tool_name: 'UserPrompt', call_id: 'c2' } },
          evidence_refs: [],
          timestamp_ns: 0,
          phase: { Provisional: { timestamp_ns: 0 } },
        },
      ],
      edges: [
        {
          edge_id: 'e1',
          edge_type: 'PolicyBoundaryCrossing',
          source: 'agent-a',
          target: 'b1',
          evidence_refs: [],
          confidence: 0.8,
          phase: { Provisional: { timestamp_ns: 0 } },
        },
        {
          edge_id: 'e2',
          edge_type: 'PolicyBoundaryCrossing',
          source: 'agent-a',
          target: 'b2',
          evidence_refs: [],
          confidence: 0.7,
          phase: { Provisional: { timestamp_ns: 0 } },
        },
      ],
    };

    const adapted = adaptGraphToLargeGraph(graph);
    expect(
      adapted.nodes.some((n) => n.node_type === 'PolicyBoundary' && n.boundary_count === 2),
    ).toBe(true);
    expect(
      adapted.links.some((l) => l.edge_type === 'PolicyBoundaryCrossing' && l.count === 2),
    ).toBe(true);
  });
});
