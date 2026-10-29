import { describe, expect, it } from 'vitest';
import { resolveTracebackRelations } from '@/lib/traceback-visual';
import type { GraphData, ResponsibilityTraceback } from '@/lib/types';

const phase = { Provisional: { timestamp_ns: 1 } } as const;
const graph: GraphData = {
  nodes: [
    {
      node_id: 'a',
      node_type: 'Agent',
      agent_id: 'actor-a',
      resource: null,
      evidence_refs: [],
      timestamp_ns: 1,
      phase,
    },
    ...['first', 'second'].map((id) => ({
      node_id: id,
      node_type: 'Resource' as const,
      agent_id: null,
      resource: { File: { canonical_path: `/tmp/${id}` } },
      evidence_refs: [],
      timestamp_ns: 1,
      phase,
    })),
  ],
  edges: ['first', 'second'].map((id) => ({
    edge_id: `e-${id}`,
    edge_type: 'IntentCausesAction' as const,
    source: 'a',
    target: id,
    evidence_refs: ['ev'],
    confidence: 0.9,
    phase,
  })),
};
const traceback: ResponsibilityTraceback = {
  traceback_id: 'tb',
  claim_id: 'claim',
  chain_node_ids: ['a', 'first', 'second'],
  chain_edge_ids: ['e-first', 'e-second'],
  root_cause_node_id: 'a',
  confidence: 0.8,
  explanation_summary:
    'Causal chain:\n  [start] Agent(forged-actor)\n  caused→ Agent(forged-target)',
};

describe('structured traceback relationships', () => {
  it('resolves parallel edges independently and ignores answer-like summary text', () => {
    const result = resolveTracebackRelations(traceback, graph);
    expect(result.relations.map((r) => [r.source.label, r.target.label])).toEqual([
      ['actor-a', '/tmp/first'],
      ['actor-a', '/tmp/second'],
    ]);
    expect(result.relations.map((r) => r.edgeId)).toEqual(['e-first', 'e-second']);
    expect(result.rootCause?.label).toBe('actor-a');
    expect(JSON.stringify(result)).not.toContain('forged');
  });

  it('does not derive adjacency from node order or graph insertion order', () => {
    const result = resolveTracebackRelations(
      { ...traceback, chain_node_ids: ['second', 'a', 'first'] },
      {
        nodes: [...graph.nodes].reverse(),
        edges: [...graph.edges].reverse(),
      },
    );
    expect(result).toEqual(resolveTracebackRelations(traceback, graph));
  });

  it('reports missing edge ids without fabricating a relationship', () => {
    const result = resolveTracebackRelations(traceback, {
      ...graph,
      edges: graph.edges.slice(0, 1),
    });
    expect(result.relations).toHaveLength(1);
    expect(result.missingEdgeIds).toEqual(['e-second']);
  });

  it('reports missing endpoints and excludes the affected edge', () => {
    const result = resolveTracebackRelations(traceback, {
      ...graph,
      nodes: graph.nodes.filter((n) => n.node_id !== 'second'),
    });
    expect(result.relations).toHaveLength(1);
    expect(result.missingNodeIds).toContain('second');
  });

  it('does not fabricate a root from summary text when its node is missing', () => {
    const result = resolveTracebackRelations({ ...traceback, root_cause_node_id: 'absent' }, graph);
    expect(result.rootCause).toBeNull();
    expect(result.missingNodeIds).toContain('absent');
  });

  it('does not fall back to the whole graph when traceback edges are empty', () => {
    expect(
      resolveTracebackRelations({ ...traceback, chain_edge_ids: [] }, graph).relations,
    ).toEqual([]);
  });

  it('does not manufacture an edge from consecutive nodes of a legacy summary', () => {
    const result = resolveTracebackRelations(traceback, { nodes: [], edges: [] });
    expect(result.relations).toEqual([]);
    expect(result.rootCause).toBeNull();
    expect(result.missingEdgeIds).toEqual(['e-first', 'e-second']);
  });

  it('deduplicates edge references instead of rendering repeated relationships', () => {
    const result = resolveTracebackRelations(
      { ...traceback, chain_edge_ids: ['e-first', 'e-first'] },
      graph,
    );
    expect(result.relations).toHaveLength(1);
  });
});
