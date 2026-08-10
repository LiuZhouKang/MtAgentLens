import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from './types';

export function collectAlertAgentIds(alert: ResponsibilityAlert): string[] {
  const ids = new Set<string>();
  for (const step of alert.timeline.steps) {
    if (step.agent_id) ids.add(step.agent_id);
  }
  ids.add(alert.primary_agent.agent_id);
  return Array.from(ids);
}

export function collectLinkedEvidence(
  alerts: ResponsibilityAlert[],
  evidenceItems: CorrelationEvidence[],
  extraRefs?: string[],
): CorrelationEvidence[] {
  const refs = new Set<string>(extraRefs ?? []);
  for (const alert of alerts) {
    for (const step of alert.timeline.steps) {
      if (step.evidence_ref) refs.add(step.evidence_ref);
    }
  }
  return evidenceItems.filter((item) => refs.has(item.evidence_id));
}

// ── Per-graph lookup indexes (WeakMap-cached) ──
// Cluster/investigation modeling performs hundreds of `graph.nodes.find` calls
// (one per cluster, repeatedly inside sort comparators). That is O(n) per call
// and dominates the cost on large graphs. These indexes turn lookups into O(1)
// while keyed by graph object identity, so they are safe for live data updates
// (a new graph object builds a fresh index) and are GC'd with the graph.
const nodeByIdCache = new WeakMap<GraphData, Map<string, GraphData['nodes'][number]>>();
const agentNodeCache = new WeakMap<GraphData, Map<string, GraphData['nodes'][number]>>();

function getNodeById(graph: GraphData, nodeId: string): GraphData['nodes'][number] | undefined {
  let index = nodeByIdCache.get(graph);
  if (!index) {
    index = new Map(graph.nodes.map((node) => [node.node_id, node]));
    nodeByIdCache.set(graph, index);
  }
  return index.get(nodeId);
}

export function findNodeById(
  graph: GraphData,
  nodeId: string,
): GraphData['nodes'][number] | undefined {
  return getNodeById(graph, nodeId);
}

export function findAgentNode(
  graph: GraphData,
  agentId: string,
): GraphData['nodes'][number] | undefined {
  let index = agentNodeCache.get(graph);
  if (!index) {
    index = new Map();
    for (const node of graph.nodes) {
      if (node.agent_id) index.set(node.agent_id, node);
    }
    agentNodeCache.set(graph, index);
  }
  return index.get(agentId);
}

export function findDirectlyConnectedNodeIds(graph: GraphData, seedNodeIds: string[]): string[] {
  const connected = new Set<string>();
  for (const edge of graph.edges) {
    if (seedNodeIds.includes(edge.source)) connected.add(edge.target);
    if (seedNodeIds.includes(edge.target)) connected.add(edge.source);
  }
  return Array.from(connected);
}

// Mirrors `formatResponsibilityNodeLabel` (investigation.ts): resource nodes
// resolve to their canonical path / host:port / pid instead of an opaque id.
function formatResourceNodeLabel(node: GraphData['nodes'][number]): string {
  if (!node.resource) return node.node_id;
  if ('File' in node.resource) return node.resource.File.canonical_path;
  if ('Network' in node.resource) {
    const { host, port } = node.resource.Network;
    return `${host}:${port}`;
  }
  if ('Process' in node.resource) return `pid:${node.resource.Process.process_id.pid}`;
  if ('ToolCall' in node.resource) return node.resource.ToolCall.tool_name;
  if ('Credential' in node.resource) return node.resource.Credential.credential_id;
  if ('Context' in node.resource) return node.resource.Context.context_id;
  if ('LlmConversation' in node.resource) {
    return `${node.resource.LlmConversation.provider}/${node.resource.LlmConversation.model}`;
  }
  return node.node_id;
}

export function formatNodeLabelById(graph: GraphData, nodeId: string): string {
  const node = getNodeById(graph, nodeId);
  if (!node) return nodeId;
  if (node.node_type === 'Agent') return node.agent_id || node.node_id;
  if (node.node_type === 'PolicyBoundary') return node.node_id;
  return formatResourceNodeLabel(node);
}
