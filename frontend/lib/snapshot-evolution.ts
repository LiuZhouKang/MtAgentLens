import type { GraphSnapshotFrame } from './types';
// pickRepresentativeFrames 在 M0 随 extractSnapshotFramesFromDb 落地于 db-import.ts，此处复用不重定义。

// ═══ 图时间演化（M4）——纯函数帧差分，无造数 ═══

export interface FrameDiff {
  addedNodes: string[];
  removedNodes: string[];
  addedEdges: string[];
  removedEdges: string[];
}

/** 边标识：source>target。 */
const edgeKey = (edge: { source: string; target: string }): string =>
  `${edge.source}>${edge.target}`;

export function buildFrameDiff(prev: GraphSnapshotFrame, next: GraphSnapshotFrame): FrameDiff {
  const prevNodes = new Set(prev.graph.nodes.map((n) => n.node_id));
  const nextNodes = new Set(next.graph.nodes.map((n) => n.node_id));
  const prevEdges = new Set(prev.graph.edges.map(edgeKey));
  const nextEdges = new Set(next.graph.edges.map(edgeKey));

  const addedNodes = next.graph.nodes.map((n) => n.node_id).filter((id) => !prevNodes.has(id));
  const removedNodes = prev.graph.nodes.map((n) => n.node_id).filter((id) => !nextNodes.has(id));
  const addedEdges = next.graph.edges.map(edgeKey).filter((k) => !prevEdges.has(k));
  const removedEdges = prev.graph.edges.map(edgeKey).filter((k) => !nextEdges.has(k));

  return { addedNodes, removedNodes, addedEdges, removedEdges };
}

export function summarizeFrameChange(diff: FrameDiff): string {
  const nodeDelta = diff.addedNodes.length - diff.removedNodes.length;
  const edgeDelta = diff.addedEdges.length - diff.removedEdges.length;
  // 排版负号 U+2212（与前端数显风格一致；避免 ASCII '-' 在等宽字体内视觉跳变）
  const sign = (n: number): string => (n >= 0 ? '+' : '−');
  return `${sign(nodeDelta)}${Math.abs(nodeDelta)} 节点 / ${sign(edgeDelta)}${Math.abs(edgeDelta)} 边`;
}
