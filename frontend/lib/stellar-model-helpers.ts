import type { CorrelationEvidence, GraphData, ResponsibilityAlert, ResponsibilityNode } from './types';
import { collectAlertEvidenceRefs, formatResponsibilityNodeLabel } from './investigation';

export function collectAlertAgentIds(alert: ResponsibilityAlert): string[] {
  return Array.from(
    new Set(
      [alert.primary_agent?.agent_id, ...alert.involved_agents.map((agent) => agent.agent_id)].filter(
        (value): value is string => !!value,
      ),
    ),
  );
}

export function collectLinkedEvidence(
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[],
  extraEvidenceRefs: string[] = [],
): CorrelationEvidence[] {
  const evidenceRefs = new Set<string>(extraEvidenceRefs);

  alerts.forEach((alert) => {
    collectAlertEvidenceRefs(alert).forEach((ref) => evidenceRefs.add(ref));
  });

  return evidence
    .filter((item) => evidenceRefs.has(item.evidence_id))
    .sort((a, b) => b.timestamp_ns - a.timestamp_ns);
}

export function findAgentNode(graph: GraphData, agentId: string): ResponsibilityNode | null {
  return graph.nodes.find((node) => node.node_id === agentId || node.agent_id === agentId) ?? null;
}

export function formatNodeLabelById(graph: GraphData, nodeId: string): string {
  const node = graph.nodes.find((item) => item.node_id === nodeId);
  if (!node) {
    return nodeId;
  }

  return formatResponsibilityNodeLabel(node);
}

export function findDirectlyConnectedNodeIds(graph: GraphData, seedNodeIds: string[]): string[] {
  const seedSet = new Set(seedNodeIds);
  const connected = new Set<string>();

  graph.edges.forEach((edge) => {
    if (seedSet.has(edge.source)) {
      connected.add(edge.target);
    }
    if (seedSet.has(edge.target)) {
      connected.add(edge.source);
    }
  });

  return Array.from(connected);
}
