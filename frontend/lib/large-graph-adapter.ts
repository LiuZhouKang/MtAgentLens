import type { GraphData, ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';

export type LargeGraphColorBy = 'node_type' | 'agent_id' | 'cluster_id';

export type LargeGraphNode = {
  id: string;
  raw_node_ids?: string[];
  node_type: ResponsibilityNode['node_type'];
  agent_id: string;
  cluster_id: string;
  description: string;
  boundary_key?: string;
  boundary_count?: number;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
};

export type LargeGraphLink = {
  id: string;
  raw_edge_ids?: string[];
  source: string | LargeGraphNode;
  target: string | LargeGraphNode;
  source_id: string;
  target_id: string;
  source_label: string;
  target_label: string;
  source_type: ResponsibilityNode['node_type'];
  target_type: ResponsibilityNode['node_type'];
  edge_type: ResponsibilityEdge['edge_type'];
  confidence: number;
  count?: number;
  boundary_key?: string;
};

export type LargeGraphData = {
  nodes: LargeGraphNode[];
  links: LargeGraphLink[];
};

type PinnedPosition = { x: number; y: number; z: number };

function fnv1a32(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function stableId(prefix: string, key: string): string {
  return `${prefix}:${fnv1a32(key).toString(16)}`;
}

function formatResource(node: ResponsibilityNode): string | null {
  if (!node.resource) return null;
  if ('File' in node.resource) return node.resource.File.canonical_path;
  if ('Network' in node.resource) return `${node.resource.Network.scheme}://${node.resource.Network.host}:${node.resource.Network.port}`;
  if ('Process' in node.resource) return `pid=${node.resource.Process.process_id.pid}`;
  if ('LlmConversation' in node.resource) return `${node.resource.LlmConversation.provider}/${node.resource.LlmConversation.model}`;
  if ('ToolCall' in node.resource) return node.resource.ToolCall.tool_name;
  if ('Credential' in node.resource) return `credential:${node.resource.Credential.credential_id}`;
  if ('Context' in node.resource) return `context:${node.resource.Context.context_id}`;
  return null;
}

export function getPolicyBoundaryKey(node: ResponsibilityNode): string {
  if (node.node_type !== 'PolicyBoundary') return 'PolicyBoundary';
  if (!node.resource) return 'PolicyBoundary:unknown';
  if ('File' in node.resource) return `File:${node.resource.File.canonical_path}`;
  if ('Network' in node.resource) return `Network:${node.resource.Network.scheme}://${node.resource.Network.host}:${node.resource.Network.port}`;
  if ('Process' in node.resource) return `Process:pid:${node.resource.Process.process_id.pid}`;
  if ('LlmConversation' in node.resource) return `LlmConversation:${node.resource.LlmConversation.provider}/${node.resource.LlmConversation.model}`;
  if ('ToolCall' in node.resource) return `ToolCall:${node.resource.ToolCall.tool_name}`;
  if ('Credential' in node.resource) return `Credential:${node.resource.Credential.credential_id}`;
  if ('Context' in node.resource) return `Context:${node.resource.Context.context_id}`;
  return 'PolicyBoundary:unknown';
}

function computeComponentClusters(nodeIds: string[], links: Array<{ source: string; target: string }>): Map<string, string> {
  const parent = new Map<string, string>();
  const rank = new Map<string, number>();

  const find = (id: string): string => {
    const current = parent.get(id);
    if (!current || current === id) {
      parent.set(id, id);
      return id;
    }
    const root = find(current);
    parent.set(id, root);
    return root;
  };

  const union = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) return;
    const rka = rank.get(ra) ?? 0;
    const rkb = rank.get(rb) ?? 0;
    if (rka < rkb) {
      parent.set(ra, rb);
      return;
    }
    if (rka > rkb) {
      parent.set(rb, ra);
      return;
    }
    parent.set(rb, ra);
    rank.set(ra, rka + 1);
  };

  nodeIds.forEach((nodeId) => {
    parent.set(nodeId, nodeId);
    rank.set(nodeId, 0);
  });

  links.forEach((link) => {
    if (!parent.has(link.source) || !parent.has(link.target)) return;
    union(link.source, link.target);
  });

  const rootToCluster = new Map<string, string>();
  let clusterIndex = 0;
  const nodeToCluster = new Map<string, string>();
  nodeIds.forEach((nodeId) => {
    const root = find(nodeId);
    let clusterId = rootToCluster.get(root);
    if (!clusterId) {
      clusterId = `cluster-${clusterIndex}`;
      clusterIndex += 1;
      rootToCluster.set(root, clusterId);
    }
    nodeToCluster.set(nodeId, clusterId);
  });
  return nodeToCluster;
}

export function adaptGraphToLargeGraph(
  graph: GraphData,
  pinned: Record<string, PinnedPosition> = {},
): LargeGraphData {
  const rawNodes = graph.nodes ?? [];
  const rawEdges = graph.edges ?? [];
  const rawNodeById = new Map(rawNodes.map((node) => [node.node_id, node]));

  const boundaryAgg = new Map<string, { id: string; key: string; count: number }>();
  const idRemap = new Map<string, string>();
  rawNodes.forEach((node) => {
    if (node.node_type !== 'PolicyBoundary') return;
    const key = getPolicyBoundaryKey(node);
    const existing = boundaryAgg.get(key);
    if (existing) {
      existing.count += 1;
      idRemap.set(node.node_id, existing.id);
      return;
    }
    const id = stableId('boundary', key);
    boundaryAgg.set(key, { id, key, count: 1 });
    idRemap.set(node.node_id, id);
  });

  const adaptedNodeMap = new Map<string, LargeGraphNode>();
  rawNodes.forEach((node) => {
    if (node.node_type === 'PolicyBoundary') return;
    const agentId = node.agent_id ?? 'unassigned';
    const resource = formatResource(node);
    const pinnedPos = pinned[node.node_id];
    adaptedNodeMap.set(node.node_id, {
      id: node.node_id,
      raw_node_ids: [node.node_id],
      node_type: node.node_type,
      agent_id: agentId,
      cluster_id: 'cluster-0',
      description: [node.node_type, agentId !== 'unassigned' ? agentId : null, resource].filter(Boolean).join(' • '),
      ...(pinnedPos
        ? {
            fx: pinnedPos.x,
            fy: pinnedPos.y,
            fz: pinnedPos.z,
          }
        : null),
    });
  });

  boundaryAgg.forEach((entry) => {
    adaptedNodeMap.set(entry.id, {
      id: entry.id,
      raw_node_ids: rawNodes
        .filter((node) => node.node_type === 'PolicyBoundary' && getPolicyBoundaryKey(node) === entry.key)
        .map((node) => node.node_id),
      node_type: 'PolicyBoundary',
      agent_id: 'unassigned',
      cluster_id: 'cluster-0',
      boundary_key: entry.key,
      boundary_count: entry.count,
      description: ['PolicyBoundary', entry.key, `events:${entry.count}`].join(' • '),
    });
  });

  const linkAgg = new Map<string, { source: string; target: string; edge_type: ResponsibilityEdge['edge_type']; count: number; maxConfidence: number; rawEdgeIds: string[] }>();
  rawEdges.forEach((edge) => {
    if (!rawNodeById.has(edge.source) || !rawNodeById.has(edge.target)) return;
    const source = idRemap.get(edge.source) ?? edge.source;
    const target = idRemap.get(edge.target) ?? edge.target;
    if (source === target) return;
    if (!adaptedNodeMap.has(source) || !adaptedNodeMap.has(target)) return;
    const key = `${source}|${target}|${edge.edge_type}`;
    const existing = linkAgg.get(key);
    if (existing) {
      existing.count += 1;
      existing.maxConfidence = Math.max(existing.maxConfidence, edge.confidence);
      existing.rawEdgeIds.push(edge.edge_id);
      return;
    }
    linkAgg.set(key, { source, target, edge_type: edge.edge_type, count: 1, maxConfidence: edge.confidence, rawEdgeIds: [edge.edge_id] });
  });

  const adaptedLinks: LargeGraphLink[] = Array.from(linkAgg.values()).map((entry) => {
    const sourceNode = adaptedNodeMap.get(entry.source);
    const targetNode = adaptedNodeMap.get(entry.target);
    return {
      id: stableId('link', `${entry.source}|${entry.target}|${entry.edge_type}`),
      raw_edge_ids: entry.rawEdgeIds,
      source: entry.source,
      target: entry.target,
      source_id: entry.source,
      target_id: entry.target,
      source_label: sourceNode?.agent_id && sourceNode.agent_id !== 'unassigned'
        ? sourceNode.agent_id
        : sourceNode?.node_type === 'PolicyBoundary'
          ? 'Policy Boundary'
          : sourceNode?.id ?? entry.source,
      target_label: targetNode?.agent_id && targetNode.agent_id !== 'unassigned'
        ? targetNode.agent_id
        : targetNode?.node_type === 'PolicyBoundary'
          ? 'Policy Boundary'
          : targetNode?.id ?? entry.target,
      source_type: sourceNode?.node_type ?? 'Resource',
      target_type: targetNode?.node_type ?? 'Resource',
      edge_type: entry.edge_type,
      confidence: entry.maxConfidence,
      count: entry.count,
      boundary_key: targetNode?.boundary_key,
    };
  });

  const nodeIds = Array.from(adaptedNodeMap.keys());
  const clusterByNode = computeComponentClusters(
    nodeIds,
    adaptedLinks.map((link) => ({
      source: typeof link.source === 'string' ? link.source : link.source.id,
      target: typeof link.target === 'string' ? link.target : link.target.id,
    })),
  );

  const adaptedNodes = nodeIds.map((id) => {
    const node = adaptedNodeMap.get(id);
    if (!node) {
      return {
        id,
        node_type: 'Resource' as const,
        agent_id: 'unassigned',
        cluster_id: clusterByNode.get(id) ?? 'cluster-0',
        description: id,
      };
    }
    return {
      ...node,
      cluster_id: clusterByNode.get(id) ?? node.cluster_id,
    };
  });

  return { nodes: adaptedNodes, links: adaptedLinks };
}
