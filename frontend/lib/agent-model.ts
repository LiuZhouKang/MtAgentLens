import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from './types';
import {
  collectAlertAgentIds,
  collectLinkedEvidence,
  findAgentNode,
  formatNodeLabelById,
} from './stellar-model-helpers';
import { summarizeResourceSurface, type ResourceCounts, type SurfacedResource } from './resource-surface';
export type AgentResourceCounts = ResourceCounts;
export type AgentSurfacedResource = SurfacedResource;

export interface AgentModel {
  agentId: string;
  parentClusterId: string;
  title: string;
  summary: string;
  alertIds: string[];
  leadAlertId: string | null;
  claimedRoles: string[];
  relatedAgentIds: string[];
  touchedResources: string[];
  surfacedResources: AgentSurfacedResource[];
  resourceCounts: AgentResourceCounts;
  linkedEvidence: CorrelationEvidence[];
  highestSeverity: ResponsibilityAlert['severity'] | null;
}

const severityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Info: 0,
  Warning: 1,
  Critical: 2,
};

export function buildAgentModel(
  agentId: string,
  graph: GraphData,
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[] = [],
): AgentModel {
  const directNode = findAgentNode(graph, agentId);
  const agentNodeId = directNode?.node_id ?? agentId;
  const relevantAlerts = alerts.filter((alert) => collectAlertAgentIds(alert).includes(agentId));
  const claimedRoles = Array.from(
    new Set(
      relevantAlerts.flatMap((alert) =>
        [alert.primary_agent, ...alert.involved_agents]
          .filter((entry) => entry.agent_id === agentId)
          .map((entry) => entry.claimed_role)
          .filter((value): value is string => !!value),
      ),
    ),
  );
  const directlyConnectedEdges = graph.edges.filter((edge) => edge.source === agentNodeId || edge.target === agentNodeId);
  const relatedAgentIds = Array.from(
    new Set(
      [
        ...relevantAlerts.flatMap((alert) => collectAlertAgentIds(alert)),
        ...directlyConnectedEdges
          .flatMap((edge) => [edge.source, edge.target])
          .filter((nodeId) => nodeId !== agentNodeId)
          .map((nodeId) => graph.nodes.find((node) => node.node_id === nodeId))
          .filter((node): node is NonNullable<typeof node> => !!node && node.node_type === 'Agent')
          .map((node) => node.agent_id ?? node.node_id),
      ].filter((value): value is string => !!value && value !== agentId),
    ),
  );
  const connectedResourceNodes = Array.from(
    new Map(
      directlyConnectedEdges
        .flatMap((edge) => [edge.source, edge.target])
        .filter((nodeId) => nodeId !== agentNodeId)
        .map((nodeId) => graph.nodes.find((node) => node.node_id === nodeId))
        .filter((node): node is NonNullable<typeof node> => !!node && node.node_type !== 'Agent')
        .map((node) => [node.node_id, node]),
    ).values(),
  );
  const touchedResources = connectedResourceNodes.map((node) => formatNodeLabelById(graph, node.node_id));
  const { resourceCounts, surfacedResources } = summarizeResourceSurface(connectedResourceNodes, graph, 8);
  const highestSeverity =
    relevantAlerts
      .map((alert) => alert.severity)
      .sort((left, right) => severityWeight[right] - severityWeight[left])[0] ?? null;
  const parentClusterId =
    relevantAlerts[0]?.primary_agent?.agent_id ??
    relatedAgentIds[0] ??
    agentId;

  return {
    agentId,
    parentClusterId,
    title: directNode ? formatNodeLabelById(graph, directNode.node_id) : agentId,
    summary:
      relevantAlerts[0]?.summary ??
      '当前尚未发现与该 agent 直接绑定的风险事件，后续接入更多实时数据后这里会显示成熟监测摘要。',
    alertIds: relevantAlerts.map((alert) => alert.alert_id),
    leadAlertId: relevantAlerts[0]?.alert_id ?? null,
    claimedRoles,
    relatedAgentIds,
    touchedResources,
    surfacedResources,
    resourceCounts,
    linkedEvidence: collectLinkedEvidence(relevantAlerts, evidence, directNode?.evidence_refs ?? []),
    highestSeverity,
  };
}
