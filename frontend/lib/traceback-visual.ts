// Structured traceback references; prose is never used to reconstruct edges.
//
// 只信任后端结构化字段：traceback.chain_edge_ids 引用 graph.edges.edge_id，
// 由边的 source/target 解析出真实端点；explanation_summary 仅作为历史文字
// 说明展示，绝不从文本推断拓扑，也不把 chain_node_ids 的相邻节点当边。
// 纯函数：只依赖输入，无副作用。

import { formatResponsibilityNodeLabel } from './investigation';
import type {
  GraphData,
  ResponsibilityEdge,
  ResponsibilityNode,
  ResponsibilityTraceback,
} from './types';

export type TracebackNodeKind =
  'agent' | 'file' | 'process' | 'network' | 'tool' | 'credential' | 'context' | 'llm' | 'unknown';

export interface TracebackStep {
  kind: TracebackNodeKind;
  /** Human-readable label; detail retains the exact node ID. */
  label: string;
  /** 附加信息（完整 agent id / 网络端口等），hover 展示 */
  detail: string | null;
}

/** 节点类型 → 中文小标签（供因果链类型徽章）。 */
export const TRACEBACK_KIND_LABEL: Record<TracebackNodeKind, string> = {
  agent: 'Agent',
  file: '文件',
  process: '进程',
  network: '网络',
  tool: '工具',
  credential: '凭证',
  context: '上下文',
  llm: 'LLM',
  unknown: '未知',
};

export interface TracebackRelation {
  edgeId: string;
  source: TracebackStep;
  target: TracebackStep;
  edgeType: ResponsibilityEdge['edge_type'];
}

export interface TracebackRelations {
  relations: TracebackRelation[];
  rootCause: TracebackStep | null;
  missingEdgeIds: string[];
  missingNodeIds: string[];
}

function nodeKind(node: ResponsibilityNode): TracebackNodeKind {
  if (node.node_type === 'Agent') return 'agent';
  if (node.node_type === 'Process') return 'process';
  if (node.node_type === 'ToolCall') return 'tool';
  if (node.node_type === 'Credential') return 'credential';
  if (node.node_type === 'Context') return 'context';

  const resource = node.resource;
  if (!resource) return 'unknown';
  if ('File' in resource) return 'file';
  if ('Network' in resource) return 'network';
  if ('Process' in resource) return 'process';
  if ('LlmConversation' in resource) return 'llm';
  if ('ToolCall' in resource) return 'tool';
  if ('Credential' in resource) return 'credential';
  if ('Context' in resource) return 'context';
  return 'unknown';
}

function toStep(node: ResponsibilityNode): TracebackStep {
  return {
    kind: nodeKind(node),
    label: formatResponsibilityNodeLabel(node),
    detail: node.node_id,
  };
}

/**
 * 依据 traceback.chain_edge_ids 解析真实图边，输出可渲染的 source→target
 * 关系。缺失的边/端点/根因如实上报，不伪造节点或相邻关系。
 * 输出顺序只由 chain_edge_ids 决定，图插入顺序不影响结果。
 */
export function resolveTracebackRelations(
  tb: ResponsibilityTraceback,
  graph: GraphData,
): TracebackRelations {
  const nodesById = new Map<string, ResponsibilityNode>();
  for (const node of graph.nodes) {
    if (!nodesById.has(node.node_id)) nodesById.set(node.node_id, node);
  }
  const edgesById = new Map<string, ResponsibilityEdge>();
  for (const edge of graph.edges) {
    if (!edgesById.has(edge.edge_id)) edgesById.set(edge.edge_id, edge);
  }

  const relations: TracebackRelation[] = [];
  const missingEdgeIds: string[] = [];
  const missingNodeIds = new Set<string>();
  const seenEdgeIds = new Set<string>();

  for (const edgeId of tb.chain_edge_ids ?? []) {
    // 重复引用只渲染一次，也不重复计入缺失。
    if (seenEdgeIds.has(edgeId)) continue;
    seenEdgeIds.add(edgeId);

    const edge = edgesById.get(edgeId);
    if (!edge) {
      missingEdgeIds.push(edgeId);
      continue;
    }

    const sourceNode = nodesById.get(edge.source);
    const targetNode = nodesById.get(edge.target);
    if (!sourceNode) missingNodeIds.add(edge.source);
    if (!targetNode) missingNodeIds.add(edge.target);
    // 端点缺失则排除该边，绝不用占位节点补造关系。
    if (!sourceNode || !targetNode) continue;

    relations.push({
      edgeId,
      source: toStep(sourceNode),
      target: toStep(targetNode),
      edgeType: edge.edge_type,
    });
  }

  // chain_node_ids 中缺失的引用同样要上报，但它们不产生边。
  for (const nodeId of tb.chain_node_ids ?? []) {
    if (!nodesById.has(nodeId)) missingNodeIds.add(nodeId);
  }

  const rootNode = tb.root_cause_node_id ? nodesById.get(tb.root_cause_node_id) : undefined;
  if (tb.root_cause_node_id && !rootNode) {
    missingNodeIds.add(tb.root_cause_node_id);
  }

  return {
    relations,
    rootCause: rootNode ? toStep(rootNode) : null,
    missingEdgeIds,
    missingNodeIds: Array.from(missingNodeIds),
  };
}
