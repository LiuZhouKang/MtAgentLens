import type { GraphData, GraphMetrics, ResponsibilityAlert } from './types';
import { deriveGraphMetrics } from './graph-metrics';

/**
 * Local data-snapshot import — the "导入本地文件" path of the data source
 * browser. Accepts JSON snapshots in the same shape the collector exports and
 * `public/data/sample-run.json` follows: a top-level object with `graph`
 * ({nodes, edges}) and `alerts` (array); `metrics` is optional and derived
 * from the graph when omitted.
 */

export class SnapshotParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SnapshotParseError';
  }
}

export type SnapshotImport = {
  /** Stable identity used as the data source key, e.g. "file:run-42.json". */
  id: string;
  label: string;
  graph: GraphData;
  alerts: ResponsibilityAlert[];
  metrics: GraphMetrics;
  nodeCount: number;
  edgeCount: number;
  alertCount: number;
};

export function parseSnapshotJson(text: string, fileName: string): SnapshotImport {
  const label = (fileName.replace(/^.*[\\/]/, '') || 'snapshot.json').trim();
  if (!text.trim()) {
    throw new SnapshotParseError('文件内容为空');
  }

  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new SnapshotParseError('不是有效的 JSON 文件');
  }

  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new SnapshotParseError('快照必须是包含 graph 与 alerts 的 JSON 对象');
  }

  const data = raw as Record<string, unknown>;
  const graph = data.graph as Record<string, unknown> | undefined;
  if (!graph || !Array.isArray(graph.nodes)) {
    throw new SnapshotParseError('数据快照缺少 graph.nodes（需含节点列表）');
  }
  if (!Array.isArray(data.alerts)) {
    throw new SnapshotParseError('数据快照缺少 alerts（需含告警数组）');
  }

  const g: GraphData = {
    nodes: graph.nodes as GraphData['nodes'],
    edges: (graph.edges as GraphData['edges']) ?? [],
  };
  const alerts = data.alerts as ResponsibilityAlert[];
  const metrics = (data.metrics as GraphMetrics) ?? deriveGraphMetrics(g);

  return {
    id: `file:${label}`,
    label,
    graph: g,
    alerts,
    metrics,
    nodeCount: g.nodes.length,
    edgeCount: g.edges.length,
    alertCount: alerts.length,
  };
}
