import type { GraphData } from '@/lib/types';
import { adaptGraphToLargeGraph, type LargeGraphData } from '@/lib/large-graph-adapter';

/**
 * LOD (level-of-detail) graph summarization.
 *
 * A 100k-node responsibility graph cannot be rendered natively in any graph
 * library. We reduce it by scale: `raw` (full fidelity) up to a few thousand
 * nodes, `agent` (policy-boundary consolidated via large-graph-adapter), and
 * `cluster` (coarse deterministic buckets) beyond that. Summaries are
 * deterministic (stable ids) so LOD switches don't cause visual jumps.
 */

export type LodLevel = 'raw' | 'agent' | 'cluster';

export interface LodNode {
  id: string;
  node_type: string;
  label: string;
  count: number;
}

export interface LodEdge {
  source: string;
  target: string;
  edge_type: string;
  count: number;
}

export interface LodGraph {
  level: LodLevel;
  nodes: LodNode[];
  edges: LodEdge[];
  rawNodeCount: number;
  rawEdgeCount: number;
}

export const LOD_RAW_LIMIT = 4_000;
export const LOD_AGENT_LIMIT = 20_000;
export const CLUSTER_BUCKETS = 24;

export function pickLodLevel(rawNodeCount: number): LodLevel {
  if (rawNodeCount <= LOD_RAW_LIMIT) return 'raw';
  if (rawNodeCount <= LOD_AGENT_LIMIT) return 'agent';
  return 'cluster';
}

function fnv1a32(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

// Coarse, deterministic bucket key for ultra-large graphs: groups agents into
// stable "constellations" so a 100k-node graph renders as ~CLUSTER_BUCKETS nodes.
function bucketId(key: string, buckets: number): string {
  return `bucket:${fnv1a32(key) % buckets}`;
}

// Direct cluster summarization — skips the heavy agent-level adapter entirely.
// O(n + e) with plain hash buckets, so a 100k-node / 500k-edge graph summarizes
// in ~250ms instead of ~835ms (and never needs a Web Worker, whose structured
// clone would cost more than it saves).
function summarizeClusterDirect(graph: GraphData, buckets: number): LodGraph {
  const bucketOfNode = new Map<string, string>();
  const counts = new Map<string, number>();

  for (const node of graph.nodes) {
    const bucket = bucketId(node.agent_id || node.node_id, buckets);
    bucketOfNode.set(node.node_id, bucket);
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
  }

  const edgeAgg = new Map<string, LodEdge>();
  for (const edge of graph.edges) {
    const src = bucketOfNode.get(edge.source);
    const tgt = bucketOfNode.get(edge.target);
    if (!src || !tgt || src === tgt) continue;
    const key = `${src}|${tgt}|${edge.edge_type}`;
    const existing = edgeAgg.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      edgeAgg.set(key, {
        source: src,
        target: tgt,
        edge_type: edge.edge_type,
        count: 1,
      });
    }
  }

  return {
    level: 'cluster',
    nodes: Array.from(counts.entries()).map(([id, count]) => ({
      id,
      node_type: 'Agent',
      label: id,
      count,
    })),
    edges: Array.from(edgeAgg.values()),
    rawNodeCount: graph.nodes.length,
    rawEdgeCount: graph.edges.length,
  };
}

export function summarizeToLod(graph: GraphData, level: 'agent' | 'cluster'): LodGraph {
  if (level === 'cluster') {
    return summarizeClusterDirect(graph, CLUSTER_BUCKETS);
  }

  const adapted: LargeGraphData = adaptGraphToLargeGraph(graph);
  const nodeCounts = new Map<string, number>();
  for (const n of adapted.nodes) nodeCounts.set(n.id, n.raw_node_ids?.length ?? 1);

  const nodeIds = new Set(adapted.nodes.map((n) => n.id));
  const edgeAgg = new Map<string, LodEdge>();

  for (const link of adapted.links) {
    const src = typeof link.source === 'string' ? link.source : link.source.id;
    const tgt = typeof link.target === 'string' ? link.target : link.target.id;
    if (src === tgt) continue;
    if (!nodeIds.has(src) || !nodeIds.has(tgt)) continue;
    const key = `${src}|${tgt}|${link.edge_type}`;
    const existing = edgeAgg.get(key);
    if (existing) {
      existing.count += link.raw_edge_ids?.length ?? 1;
    } else {
      edgeAgg.set(key, {
        source: src,
        target: tgt,
        edge_type: link.edge_type,
        count: link.raw_edge_ids?.length ?? 1,
      });
    }
  }

  return {
    level,
    nodes: adapted.nodes.map((n) => ({
      id: n.id,
      node_type: n.node_type,
      label: n.agent_id || n.id,
      count: nodeCounts.get(n.id) ?? 1,
    })),
    edges: Array.from(edgeAgg.values()),
    rawNodeCount: graph.nodes.length,
    rawEdgeCount: graph.edges.length,
  };
}
