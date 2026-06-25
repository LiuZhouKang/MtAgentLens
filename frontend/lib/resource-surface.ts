import type { GraphData } from './types';
import { formatNodeLabelById } from './stellar-model-helpers';

export type ResourceCounts = {
  files: number;
  networks: number;
  tools: number;
  processes: number;
  contexts: number;
  boundaries: number;
  other: number;
};

export interface SurfacedResource {
  nodeId: string;
  label: string;
  bucket: keyof ResourceCounts;
  evidenceCount: number;
}

export function createEmptyResourceCounts(): ResourceCounts {
  return {
    files: 0,
    networks: 0,
    tools: 0,
    processes: 0,
    contexts: 0,
    boundaries: 0,
    other: 0,
  };
}

export function resourceBucket(node: GraphData['nodes'][number]) {
  if (node.node_type === 'PolicyBoundary') return 'boundaries' as const;
  if (node.node_type !== 'Resource' || !node.resource) return 'other' as const;
  if ('File' in node.resource) return 'files' as const;
  if ('Network' in node.resource) return 'networks' as const;
  if ('ToolCall' in node.resource) return 'tools' as const;
  if ('Process' in node.resource) return 'processes' as const;
  if ('Context' in node.resource || 'LlmConversation' in node.resource) return 'contexts' as const;
  return 'other' as const;
}

function surfacedResourcePriority(node: GraphData['nodes'][number], graph: GraphData) {
  const label = formatNodeLabelById(graph, node.node_id);
  const bucket = resourceBucket(node);

  const bucketWeight = {
    boundaries: 0,
    files: 1,
    networks: 2,
    tools: 3,
    processes: 4,
    contexts: 5,
    other: 6,
  } as const;

  let penalty = 0;

  if (label.includes('/workspace/.mtagentlens/')) penalty += 5;
  if (label.startsWith('pid:')) penalty += 4;
  if (label.startsWith('ctx-')) penalty += 4;
  if (label.includes('metadata.google.internal')) penalty += 3;
  if (label === 'browser' || label === 'git') penalty += 2;

  const evidenceBoost = node.evidence_refs.length ? -2 : 0;

  return bucketWeight[bucket] + penalty + evidenceBoost;
}

export function summarizeResourceSurface(
  nodes: GraphData['nodes'],
  graph: GraphData,
  limit = 8,
): { surfacedResources: SurfacedResource[]; resourceCounts: ResourceCounts } {
  const resourceCounts = nodes.reduce<ResourceCounts>((counts, node) => {
    counts[resourceBucket(node)] += 1;
    return counts;
  }, createEmptyResourceCounts());

  const surfacedResources = nodes
    .slice()
    .sort((left, right) => {
      const leftPriority = surfacedResourcePriority(left, graph);
      const rightPriority = surfacedResourcePriority(right, graph);

      if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
      }

      if (left.evidence_refs.length !== right.evidence_refs.length) {
        return right.evidence_refs.length - left.evidence_refs.length;
      }

      return left.timestamp_ns - right.timestamp_ns;
    })
    .map((node) => ({
      nodeId: node.node_id,
      label: formatNodeLabelById(graph, node.node_id),
      bucket: resourceBucket(node),
      evidenceCount: node.evidence_refs.length,
    }))
    .slice(0, limit);

  return {
    surfacedResources,
    resourceCounts,
  };
}
