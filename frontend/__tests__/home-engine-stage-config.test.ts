import { describe, expect, it } from 'vitest';
import type { GraphData, ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';
import { buildForceLink, buildForceNode, createBackgroundGraph } from '@/components/ui/home-engine-stage-config';

function makeNode(node_id: string, node_type: ResponsibilityNode['node_type']): ResponsibilityNode {
  return {
    node_id,
    node_type,
    agent_id: node_type === 'Agent' ? node_id : null,
    resource: null,
    evidence_refs: [],
    timestamp_ns: 1,
    phase: { Provisional: { timestamp_ns: 1 } },
  };
}

function makeEdge(edge_id: string, edge_type: ResponsibilityEdge['edge_type'], source: string, target: string): ResponsibilityEdge {
  return {
    edge_id,
    edge_type,
    source,
    target,
    evidence_refs: [],
    confidence: 0.9,
    phase: { Provisional: { timestamp_ns: 1 } },
  };
}

describe('home engine stage config', () => {
  it('keeps foreground nodes lightweight and free-floating', () => {
    const agent = buildForceNode(makeNode('agent:root', 'Agent'));
    const resource = buildForceNode(makeNode('file:/tmp/demo', 'Resource'));
    const boundary = buildForceNode(makeNode('boundary:prod', 'PolicyBoundary'));

    expect(agent.val).toBeLessThan(3.2);
    expect(resource.val).toBeLessThan(agent.val);
    expect(boundary.val).toBeLessThan(agent.val);

    expect(agent.fx).toBeUndefined();
    expect(resource.fx).toBeUndefined();
    expect(boundary.fx).toBeUndefined();

    expect(Math.hypot(agent.x ?? 0, agent.y ?? 0, agent.z ?? 0)).toBeLessThan(110);
    expect(Math.hypot(boundary.x ?? 0, boundary.y ?? 0, boundary.z ?? 0)).toBeGreaterThan(180);
  });

  it('keeps default links thin before highlight styling is applied', () => {
    const boundary = buildForceLink(makeEdge('edge:boundary', 'PolicyBoundaryCrossing', 'a', 'b'));
    const delegation = buildForceLink(makeEdge('edge:delegate', 'AgentDelegatesTo', 'a', 'b'));
    const plain = buildForceLink(makeEdge('edge:intent', 'IntentCausesAction', 'a', 'b'));

    expect(boundary.width).toBeLessThan(1);
    expect(delegation.width).toBeLessThan(boundary.width);
    expect(plain.width).toBeLessThan(delegation.width);
    expect(plain.particles).toBe(0);
  });

  it('builds a denser background graph than the foreground input', () => {
    const graph: GraphData = {
      nodes: [
        makeNode('agent:root', 'Agent'),
        makeNode('resource:1', 'Resource'),
        makeNode('boundary:prod', 'PolicyBoundary'),
      ],
      edges: [
        makeEdge('edge:1', 'AgentDelegatesTo', 'agent:root', 'resource:1'),
        makeEdge('edge:2', 'PolicyBoundaryCrossing', 'resource:1', 'boundary:prod'),
      ],
    };

    const background = createBackgroundGraph(graph);

    expect(background.nodes.length).toBeGreaterThan(graph.nodes.length * 20);
    expect(background.links.length).toBeGreaterThan(graph.edges.length * 20);
    expect(background.nodes.every((node) => node.val < 0.2)).toBe(true);
  });
});
