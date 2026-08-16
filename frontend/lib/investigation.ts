import { deriveTracebackEdgeIds } from './traceback';
import { claimTypeLabel, decisionLabel } from './investigation-labels';
import type {
  CorrelationEvidence,
  GraphData,
  ResponsibilityAlert,
  ResponsibilityEdge,
  ResponsibilityNode,
} from './types';

export interface InvestigationNodeSummary {
  nodeId: string;
  label: string;
  nodeType: ResponsibilityNode['node_type'];
}

// ── 英文分析摘要 → 结构化中文（让用户一眼看懂） ──
// Rust 后端的 alert.summary 是英文长文本（"Analysis of responsibility graph:
// 317 agent(s)... Claim #1: ... Moderate confidence ..."），无法直读。这里解析
// 为简洁中文：发生了什么、图规模、真实置信度、判定结论 —— 诚实呈现真实数据，
// 绝不伪造。
const DETECTION_LABEL: Record<string, string> = {
  'tainted context use': '上下文污染使用',
  'policy violation': '策略违规',
  'context injection': '上下文注入',
  'delegation issue': '委派异常',
  'authority escalation': '权限升级',
};

function detectionTypeLabel(raw: string): string {
  const norm = raw.trim().toLowerCase();
  return DETECTION_LABEL[norm] ?? claimTypeLabel(raw.replace(/\s+/g, '')).label;
}

// 英文告警标题 → 中文：提取检测类型 + 判定方式 + 主体
// "Observer reviewed: agent c7844354-66f — Tainted Context Use (Observed)"
// → "上下文污染使用 · 已观测（主体 c7844354…）"
const DETECTION_MODE_LABEL: Record<string, string> = {
  Observed: '已观测',
  Inferred: '推断',
  Detected: '已检测',
  Confirmed: '已确认',
};

export function buildAnalysisTitle(alert: ResponsibilityAlert): string {
  const t = alert.title ?? '';
  const typeM = t.match(/—\s*(.+?)\s*\((\w+)\)\s*$/);
  if (typeM) {
    const typeLabel = claimTypeLabel(typeM[1].replace(/\s+/g, '')).label;
    const modeLabel = DETECTION_MODE_LABEL[typeM[2]] ?? typeM[2];
    const agentShort = alert.primary_agent.agent_id.slice(0, 8);
    return `${typeLabel} · ${modeLabel}（主体 ${agentShort}…）`;
  }
  return t;
}

export function buildAnalysisSummary(alert: ResponsibilityAlert): string {
  const s = alert.summary ?? '';
  const parts: string[] = [];

  // 检测结论：Detected: 2 tainted context use(s).
  const detect = s.match(/Detected:\s*(\d+)\s+(.+?)(?:\(s\))?\./i);
  if (detect) parts.push(`检测到 ${detect[1]} 次${detectionTypeLabel(detect[2])}`);

  // 图规模：317 agent(s), 1138 node(s), 3008 edge(s).
  const agents = s.match(/(\d+)\s+agent\(s\)/i)?.[1];
  const nodes = s.match(/(\d+)\s+node\(s\)/i)?.[1];
  const edges = s.match(/(\d+)\s+edge\(s\)/i)?.[1];
  if (agents) parts.push(`${agents} 主体 · ${nodes} 节点 · ${edges} 边`);

  // 真实置信度 + 判定结论（结构化字段，非 summary 文本）
  parts.push(`置信度 ${Math.round(alert.confidence * 100)}%`);
  parts.push(`判定 ${decisionLabel(alert.decision).label}`);

  return parts.join(' · ');
}

export interface InvestigationModel {
  alertId: string;
  title: string;
  summary: string;
  severity: ResponsibilityAlert['severity'];
  confidence: number;
  authority: ResponsibilityAlert['authority'];
  decision: ResponsibilityAlert['decision'];
  primaryAgentId: string | null;
  involvedAgentIds: string[];
  timelineSteps: ResponsibilityAlert['timeline']['steps'];
  timelineDurationMs: number;
  evidenceRefs: string[];
  linkedEvidence: CorrelationEvidence[];
  pathEdges: ResponsibilityEdge[];
  pathNodes: InvestigationNodeSummary[];
  boundaryCrossingCount: number;
  ruleCount: number;
  patternCount: number;
  parentUnitId: string | null;
}

function getActiveGraph(alert: ResponsibilityAlert, fallbackGraph: GraphData): GraphData {
  if (alert.graph_snapshot?.nodes?.length || alert.graph_snapshot?.edges?.length) {
    return {
      nodes: alert.graph_snapshot.nodes,
      edges: alert.graph_snapshot.edges,
    };
  }

  return fallbackGraph;
}

// 模块级缓存：同一 (alertId, 图指纹) 的 evidence refs 只算一次。
// 59MB db 的图有数千边，13 个告警 × O(edges) 重复扫描会拖慢模型构建
// （useAlertModels rIC 分块）→ 侧边栏「构建中」长时间转。缓存消除重复扫描。
const EVIDENCE_REFS_CACHE = new Map<string, string[]>();

export function collectAlertEvidenceRefs(
  alert: ResponsibilityAlert,
  fallbackGraph: GraphData = { nodes: [], edges: [] },
  opts?: { skipAllEdgesFallback?: boolean },
): string[] {
  // Store alerts (db import / sample / demo) carry no graph_snapshot — the full
  // graph lives in the store. Derive evidence refs from the same effective graph
  // used for rendering (getActiveGraph), so evidence still links to alerts.
  const graph = getActiveGraph(alert, fallbackGraph);
  const key = `${alert.alert_id}|${graph.nodes.length}|${graph.edges.length}|${opts?.skipAllEdgesFallback ?? false}`;
  if (!opts?.skipAllEdgesFallback) {
    const hit = EVIDENCE_REFS_CACHE.get(key);
    if (hit) return hit;
  }
  const refs = new Set<string>();
  const relevantEdgeIds = new Set(deriveTracebackEdgeIds(alert, graph.edges));

  for (const step of alert.timeline?.steps ?? []) {
    if (step.evidence_ref) refs.add(step.evidence_ref);
  }

  for (const edge of graph.edges ?? []) {
    // 严格模式：相关边集为空时不回退收集全图边（默认回退是证据面板的 store 告警
    // 联动所需；分面过桥必须只采纳告警自身证据，否则任何图边证据都会虚高命中）。
    if (opts?.skipAllEdgesFallback && relevantEdgeIds.size === 0) break;
    if (relevantEdgeIds.size > 0 && !relevantEdgeIds.has(edge.edge_id)) continue;
    for (const ref of edge.evidence_refs ?? []) {
      refs.add(ref);
    }
  }

  const result = Array.from(refs).sort();
  if (!opts?.skipAllEdgesFallback) EVIDENCE_REFS_CACHE.set(key, result);
  return result;
}

export function formatResponsibilityNodeLabel(node: ResponsibilityNode): string {
  if (node.node_type === 'Agent') {
    return node.agent_id || node.node_id;
  }

  if (node.node_type === 'PolicyBoundary') {
    return node.node_id;
  }

  if (node.resource) {
    if ('File' in node.resource) return node.resource.File.canonical_path;
    if ('Network' in node.resource) {
      const { host, port } = node.resource.Network;
      return `${host}:${port}`;
    }
    if ('Process' in node.resource) {
      return `pid:${node.resource.Process.process_id.pid}`;
    }
    if ('ToolCall' in node.resource) return node.resource.ToolCall.tool_name;
    if ('Credential' in node.resource) return node.resource.Credential.credential_id;
    if ('Context' in node.resource) return node.resource.Context.context_id;
    if ('LlmConversation' in node.resource) {
      return `${node.resource.LlmConversation.provider}/${node.resource.LlmConversation.model}`;
    }
  }

  return node.node_id;
}

export function buildInvestigationModel(
  alert: ResponsibilityAlert,
  fallbackGraph: GraphData,
  evidence: CorrelationEvidence[] = [],
): InvestigationModel {
  const graph = getActiveGraph(alert, fallbackGraph);
  const evidenceRefs = collectAlertEvidenceRefs(alert, fallbackGraph);
  const pathEdgeIds = deriveTracebackEdgeIds(alert, graph.edges);
  const pathEdges = graph.edges.filter((edge) => pathEdgeIds.includes(edge.edge_id));
  const pathNodeIds = new Set<string>();

  pathEdges.forEach((edge) => {
    pathNodeIds.add(edge.source);
    pathNodeIds.add(edge.target);
  });

  if (!pathNodeIds.size && alert.primary_agent?.agent_id) {
    pathNodeIds.add(alert.primary_agent.agent_id);
  }

  const pathNodes = graph.nodes
    .filter((node) => pathNodeIds.has(node.node_id))
    .map((node) => ({
      nodeId: node.node_id,
      nodeType: node.node_type,
      label: formatResponsibilityNodeLabel(node),
    }));

  const evidenceRefSet = new Set(evidenceRefs);
  const linkedEvidence = evidence
    .filter((item) => evidenceRefSet.has(item.evidence_id))
    .sort((a, b) => a.timestamp_ns - b.timestamp_ns);

  return {
    alertId: alert.alert_id,
    title: alert.title,
    summary: alert.summary,
    severity: alert.severity,
    confidence: alert.confidence,
    authority: alert.authority,
    decision: alert.decision,
    primaryAgentId: alert.primary_agent?.agent_id ?? null,
    involvedAgentIds: Array.from(
      new Set(
        [
          alert.primary_agent?.agent_id,
          ...alert.involved_agents.map((agent) => agent.agent_id),
        ].filter((value): value is string => !!value),
      ),
    ),
    timelineSteps: alert.timeline.steps,
    timelineDurationMs: alert.timeline.total_duration_ms,
    evidenceRefs,
    linkedEvidence,
    pathEdges,
    pathNodes,
    boundaryCrossingCount: pathEdges.filter((edge) => edge.edge_type === 'PolicyBoundaryCrossing')
      .length,
    ruleCount: alert.engine.rule_verdicts.length,
    patternCount: alert.engine.pattern_verdicts.length,
    parentUnitId: alert.primary_agent?.agent_id ?? null,
  };
}
