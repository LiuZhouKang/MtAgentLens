import { deriveTracebackEdgeIds } from './traceback';
import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from './types';
import {
  collectAlertAgentIds,
  collectLinkedEvidence,
  findAgentNode,
  findDirectlyConnectedNodeIds,
  formatNodeLabelById,
} from './stellar-model-helpers';

export interface ClusterModel {
  clusterId: string;
  title: string;
  summary: string;
  alertIds: string[];
  leadAlertId: string | null;
  relatedAgentIds: string[];
  relatedAgentLabels: string[];
  highlightEdgeIds: string[];
  linkedEvidence: CorrelationEvidence[];
  boundaryCrossingCount: number;
  highestSeverity: ResponsibilityAlert['severity'] | null;
}

const severityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Info: 0,
  Warning: 1,
  Critical: 2,
};

export function buildClusterModel(
  clusterId: string,
  graph: GraphData,
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[] = [],
): ClusterModel {
  const relevantAlerts = alerts.filter((alert) => collectAlertAgentIds(alert).includes(clusterId));
  const directNode = findAgentNode(graph, clusterId);
  const connectedNodeIds = directNode ? findDirectlyConnectedNodeIds(graph, [directNode.node_id]) : [];
  const relatedAgentIds = Array.from(
    new Set(
      [
        ...(directNode || relevantAlerts.length ? [clusterId] : []),
        ...relevantAlerts.flatMap((alert) => collectAlertAgentIds(alert)),
        ...connectedNodeIds
          .map((nodeId) => graph.nodes.find((node) => node.node_id === nodeId))
          .filter((node): node is NonNullable<typeof node> => !!node && node.node_type === 'Agent')
          .map((node) => node.agent_id ?? node.node_id),
      ].filter((value): value is string => !!value),
    ),
  );
  const highlightEdgeIds = Array.from(
    new Set(relevantAlerts.flatMap((alert) => deriveTracebackEdgeIds(alert, alert.graph_snapshot?.edges ?? graph.edges))),
  );
  const highlightEdges = graph.edges.filter((edge) => highlightEdgeIds.includes(edge.edge_id));
  const highestSeverity =
    relevantAlerts
      .map((alert) => alert.severity)
      .sort((left, right) => severityWeight[right] - severityWeight[left])[0] ?? null;

  return {
    clusterId,
    title: directNode ? formatNodeLabelById(graph, directNode.node_id) : clusterId,
    summary:
      relevantAlerts[0]?.summary ??
      '当前尚未发现与该集群直接绑定的风险事件，后续接入真实 cluster 识别后这里会显示成熟监测摘要。',
    alertIds: relevantAlerts.map((alert) => alert.alert_id),
    leadAlertId: relevantAlerts[0]?.alert_id ?? null,
    relatedAgentIds,
    relatedAgentLabels: relatedAgentIds.map((agentId) => formatNodeLabelById(graph, agentId)),
    highlightEdgeIds,
    linkedEvidence: collectLinkedEvidence(relevantAlerts, evidence, directNode?.evidence_refs ?? []),
    boundaryCrossingCount: highlightEdges.filter((edge) => edge.edge_type === 'PolicyBoundaryCrossing').length,
    highestSeverity,
  };
}
