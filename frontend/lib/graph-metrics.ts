import type { GraphData, GraphMetrics } from './types';

/** Derive aggregate metrics from a responsibility graph. */
export function deriveGraphMetrics(graph: GraphData): GraphMetrics {
  const node_counts = graph.nodes.reduce<Record<string, number>>((acc, node) => {
    acc[node.node_type] = (acc[node.node_type] ?? 0) + 1;
    return acc;
  }, {});

  const edge_counts = graph.edges.reduce<Record<string, number>>((acc, edge) => {
    acc[edge.edge_type] = (acc[edge.edge_type] ?? 0) + 1;
    return acc;
  }, {});

  const cross_agent_edges = graph.edges.filter((edge) => {
    const source = graph.nodes.find((node) => node.node_id === edge.source);
    const target = graph.nodes.find((node) => node.node_id === edge.target);
    return source?.agent_id && target?.agent_id && source.agent_id !== target.agent_id;
  }).length;

  const avg_confidence = graph.edges.length
    ? graph.edges.reduce((sum, edge) => sum + edge.confidence, 0) / graph.edges.length
    : 0;

  return {
    node_counts,
    edge_counts,
    agent_count: node_counts.Agent ?? 0,
    cross_agent_edges,
    delegation_depth: edge_counts.AgentDelegatesTo ?? 0,
    taint_chains: edge_counts.TaintPropagatesTo ?? 0,
    avg_confidence,
  };
}
