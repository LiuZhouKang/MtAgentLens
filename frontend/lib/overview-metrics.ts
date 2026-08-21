// 仪表盘真实数据派生——铁律：任何 KPI/趋势/分布由纯函数从真实数据派生；
// 算不出的诚实返回空（不造数、不用 Math.sin 模拟）。

import type { ResponsibilityAlert } from './types';

/** 任意快照帧归一化后的拓扑计数。 */
export interface TopologyCounts {
  nodes: number;
  edges: number;
  agents: number;
}

/** 归一化单帧拓扑计数（兼容 live GraphSnapshot 与 db GraphSnapshotFrame）。 */
export function frameTopologyCounts(frame: unknown): TopologyCounts {
  if (!frame || typeof frame !== 'object') return { nodes: 0, edges: 0, agents: 0 };
  const f = frame as {
    metrics?: {
      node_counts?: Record<string, number>;
      edge_counts?: Record<string, number>;
      agent_count?: number;
    };
    graph?: { nodes?: unknown[]; edges?: unknown[] };
    nodes?: unknown[];
    edges?: unknown[];
  };
  if (f.metrics) {
    return {
      nodes: Object.values(f.metrics.node_counts ?? {}).reduce((s, n) => s + (n ?? 0), 0),
      edges: Object.values(f.metrics.edge_counts ?? {}).reduce((s, n) => s + (n ?? 0), 0),
      agents: Number(f.metrics.agent_count ?? 0),
    };
  }
  const g = f.graph ?? f;
  return {
    nodes: g?.nodes?.length ?? 0,
    edges: g?.edges?.length ?? 0,
    agents: 0,
  };
}

/** 真实拓扑演化趋势：按快照帧时间序派生每帧节点/边/agent 计数。
 *  少于 2 帧返回空数组（诚实空态，不造曲线）。 */
export function deriveTopologyTrend(frames: unknown[]): Array<{ name: string; nodes: number; edges: number; agents: number }> {
  if (frames.length < 2) return [];
  return frames.map((frame, i) => {
    const c = frameTopologyCounts(frame);
    return { name: `#${i + 1}`, nodes: c.nodes, edges: c.edges, agents: c.agents };
  });
}

/** 真实告警严重度分布。 */
export function deriveSeverityBreakdown(alerts: ResponsibilityAlert[]): {
  critical: number;
  warning: number;
  info: number;
} {
  const out = { critical: 0, warning: 0, info: 0 };
  for (const a of alerts) {
    if (a.severity === 'Critical') out.critical += 1;
    else if (a.severity === 'Warning') out.warning += 1;
    else out.info += 1;
  }
  return out;
}

/** 证据匹配精确度等级（官方后端枚举，L1 最精确 → L5 最宽松）。 */
export const EVIDENCE_MATCH_ORDER = ['L1Exact', 'L2Normalized', 'L3Containment', 'L4Basename', 'L5Substring'];
/** 官方枚举 → 完整中文语义（对齐 argument_matcher.rs 后端定义，完整显示不缩写）。 */
export const EVIDENCE_MATCH_MEANING: Record<string, string> = {
  L1Exact: '路径 / URL / 命令完全相等',
  L2Normalized: '归一化后相等（~ 展开、./ 与 .. 解析、命令大小写、同主机异端口）',
  L3Containment: 'URL 主机匹配 / 路径前缀或后缀包含',
  L4Basename: '文件名 / 命令名 basename 一致',
  L5Substring: '子串匹配（命令 ≥3 字符、路径 ≥8 字符）',
};
/** 官方枚举 → 图表横轴短标签（L1…L5，避免长枚举名拥挤）。 */
export const EVIDENCE_MATCH_SHORT: Record<string, string> = {
  L1Exact: 'L1',
  L2Normalized: 'L2',
  L3Containment: 'L3',
  L4Basename: 'L4',
  L5Substring: 'L5',
};
/** 可靠性语义配色：L1 精确（可靠，绿）→ L5 子串（宽松，红），一眼传达证据质量梯度。 */
export const EVIDENCE_MATCH_COLORS: Record<string, string> = {
  L1Exact: '#34d399',
  L2Normalized: '#2dd4bf',
  L3Containment: '#fbbf24',
  L4Basename: '#fb923c',
  L5Substring: '#f87171',
};

/** 真实证据精确度分布：按全量关联证据的 match_level（官方枚举）分桶。
 *  始终返回完整 L1→L5 全谱（缺失等级如实计 0，视觉完整、绝不臆造计数）。 */
export function deriveEvidenceMatchDistribution(
  evidenceItems: Array<{ match_level?: string }>,
): {
  total: number;
  byLevel: Array<{ name: string; short: string; value: number; percent: number }>;
} {
  const counts = new Map<string, number>();
  for (const item of evidenceItems) {
    const level = item.match_level ?? 'Unknown';
    counts.set(level, (counts.get(level) ?? 0) + 1);
  }
  const total = evidenceItems.length;
  const byLevel = EVIDENCE_MATCH_ORDER.map((l) => ({
    name: l,
    short: EVIDENCE_MATCH_SHORT[l],
    value: counts.get(l) ?? 0,
    percent: total > 0 ? ((counts.get(l) ?? 0) / total) * 100 : 0,
  }));
  return { total, byLevel };
}

/** 真实责任单元压力：每单元边界穿越数（top 6），用于责任单元压力条。 */
export function deriveResponsibilityUnitPressure(
  responsibilityUnits: Array<{ unitId: string; boundaryCrossingCount: number }>,
): Array<{ name: string; value: number }> {
  return responsibilityUnits.slice(0, 6).map((m) => ({ name: m.unitId, value: m.boundaryCrossingCount }));
}
