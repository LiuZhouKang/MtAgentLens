import { deriveTracebackEdgeIds } from './traceback';
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

export function collectAlertEvidenceRefs(alert: ResponsibilityAlert): string[] {
  const refs = new Set<string>();
  const relevantEdgeIds = new Set(deriveTracebackEdgeIds(alert, alert.graph_snapshot?.edges ?? []));

  for (const step of alert.timeline?.steps ?? []) {
    if (step.evidence_ref) refs.add(step.evidence_ref);
  }

  for (const edge of alert.graph_snapshot?.edges ?? []) {
    if (relevantEdgeIds.size > 0 && !relevantEdgeIds.has(edge.edge_id)) continue;
    for (const ref of edge.evidence_refs ?? []) {
      refs.add(ref);
    }
  }

  return Array.from(refs).sort();
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
  const evidenceRefs = collectAlertEvidenceRefs(alert);
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
        [alert.primary_agent?.agent_id, ...alert.involved_agents.map((agent) => agent.agent_id)].filter(
          (value): value is string => !!value,
        ),
      ),
    ),
    timelineSteps: alert.timeline.steps,
    timelineDurationMs: alert.timeline.total_duration_ms,
    evidenceRefs,
    linkedEvidence,
    pathEdges,
    pathNodes,
    boundaryCrossingCount: graph.edges.filter((edge) => edge.edge_type === 'PolicyBoundaryCrossing').length,
    ruleCount: alert.engine.rule_verdicts.length,
    patternCount: alert.engine.pattern_verdicts.length,
  };
}
