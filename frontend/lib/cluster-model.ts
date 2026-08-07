import { deriveTracebackEdgeIds } from './traceback';
import { deriveClusterIds } from './stellar-directory';
import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from './types';
import {
  collectAlertAgentIds,
  collectLinkedEvidence,
  findAgentNode,
  findDirectlyConnectedNodeIds,
  findNodeById,
  formatNodeLabelById,
} from './stellar-model-helpers';
import {
  summarizeResourceSurface,
  type ResourceCounts,
  type SurfacedResource,
} from './resource-surface';

export interface ClusterAgentLane {
  agentId: string;
  label: string;
  alertCount: number;
  primaryAlertCount: number;
  highlightedEdgeCount: number;
  boundaryCrossingCount: number;
  linkedEvidenceCount: number;
}

export interface ClusterModel {
  clusterId: string;
  title: string;
  summary: string;
  alertIds: string[];
  primaryAlertIds: string[];
  supportingAlertIds: string[];
  leadAlertId: string | null;
  leadAgentId: string | null;
  relatedAgentIds: string[];
  relatedAgentLabels: string[];
  agentLanes: ClusterAgentLane[];
  highlightEdgeIds: string[];
  linkedEvidence: CorrelationEvidence[];
  surfacedResources: SurfacedResource[];
  resourceCounts: ResourceCounts;
  boundaryCrossingCount: number;
  delegationEdgeCount: number;
  highestSeverity: ResponsibilityAlert['severity'] | null;
  pressureScore: number;
}

const severityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Info: 0,
  Warning: 1,
  Critical: 2,
};

function severityBoost(severity: ResponsibilityAlert['severity'] | null) {
  if (severity === 'Critical') return 34;
  if (severity === 'Warning') return 18;
  return 8;
}

function compareAlerts(left: ResponsibilityAlert, right: ResponsibilityAlert) {
  const severityDelta = severityWeight[right.severity] - severityWeight[left.severity];
  if (severityDelta !== 0) {
    return severityDelta;
  }

  return right.timestamp_ns - left.timestamp_ns;
}

export function compareClusterModels(left: ClusterModel, right: ClusterModel) {
  if (left.pressureScore !== right.pressureScore) {
    return right.pressureScore - left.pressureScore;
  }

  const leftSelfAnchored = left.leadAgentId === left.clusterId ? 1 : 0;
  const rightSelfAnchored = right.leadAgentId === right.clusterId ? 1 : 0;
  if (leftSelfAnchored !== rightSelfAnchored) {
    return rightSelfAnchored - leftSelfAnchored;
  }

  const severityDelta =
    severityWeight[right.highestSeverity ?? 'Info'] -
    severityWeight[left.highestSeverity ?? 'Info'];
  if (severityDelta !== 0) {
    return severityDelta;
  }

  if (left.boundaryCrossingCount !== right.boundaryCrossingCount) {
    return right.boundaryCrossingCount - left.boundaryCrossingCount;
  }

  if (left.linkedEvidence.length !== right.linkedEvidence.length) {
    return right.linkedEvidence.length - left.linkedEvidence.length;
  }

  return left.clusterId.localeCompare(right.clusterId);
}

export function buildClusterModel(
  clusterId: string,
  graph: GraphData,
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[] = [],
): ClusterModel {
  const relevantAlerts = alerts.filter((alert) => collectAlertAgentIds(alert).includes(clusterId));
  const primaryAlerts = relevantAlerts.filter(
    (alert) => alert.primary_agent?.agent_id === clusterId,
  );
  const supportingAlerts = relevantAlerts.filter(
    (alert) => alert.primary_agent?.agent_id !== clusterId,
  );
  const anchorAlerts = (primaryAlerts.length ? primaryAlerts : relevantAlerts)
    .slice()
    .sort(compareAlerts);
  const directNode = findAgentNode(graph, clusterId);
  const connectedNodeIds = directNode
    ? findDirectlyConnectedNodeIds(graph, [directNode.node_id])
    : [];
  const relatedAgentIds = Array.from(
    new Set(
      [
        ...(directNode || relevantAlerts.length ? [clusterId] : []),
        ...relevantAlerts.flatMap((alert) => collectAlertAgentIds(alert)),
        ...connectedNodeIds
          .map((nodeId) => findNodeById(graph, nodeId))
          .filter((node): node is NonNullable<typeof node> => !!node && node.node_type === 'Agent')
          .map((node) => node.agent_id ?? node.node_id),
      ].filter((value): value is string => !!value),
    ),
  );
  const relatedAgentNodeIds = new Set(
    relatedAgentIds.map((agentId) => findAgentNode(graph, agentId)?.node_id ?? agentId),
  );
  const highlightEdgeIds = Array.from(
    new Set(
      relevantAlerts.flatMap((alert) =>
        deriveTracebackEdgeIds(alert, alert.graph_snapshot?.edges ?? graph.edges),
      ),
    ),
  );
  const highlightEdgeIdSet = new Set(highlightEdgeIds);
  const highlightEdges = graph.edges.filter((edge) => highlightEdgeIdSet.has(edge.edge_id));
  const connectedResourceNodes = Array.from(
    new Map(
      graph.edges
        .filter(
          (edge) => relatedAgentNodeIds.has(edge.source) || relatedAgentNodeIds.has(edge.target),
        )
        .flatMap((edge) => [edge.source, edge.target])
        .filter((nodeId) => !relatedAgentNodeIds.has(nodeId))
        .map((nodeId) => findNodeById(graph, nodeId))
        .filter((node): node is NonNullable<typeof node> => !!node && node.node_type !== 'Agent')
        .map((node) => [node.node_id, node]),
    ).values(),
  );
  const { surfacedResources, resourceCounts } = summarizeResourceSurface(
    connectedResourceNodes,
    graph,
    6,
  );
  const highestSeverity =
    relevantAlerts
      .map((alert) => alert.severity)
      .sort((left, right) => severityWeight[right] - severityWeight[left])[0] ?? null;
  const agentLanes = relatedAgentIds
    .map((agentId) => {
      const agentNodeId = findAgentNode(graph, agentId)?.node_id ?? agentId;
      const laneEdgeIds = new Set(
        highlightEdges
          .filter((edge) => edge.source === agentNodeId || edge.target === agentNodeId)
          .map((edge) => edge.edge_id),
      );
      const laneEvidenceIds = new Set<string>();

      relevantAlerts.forEach((alert) => {
        if (!collectAlertAgentIds(alert).includes(agentId)) {
          return;
        }

        collectLinkedEvidence([alert], evidence).forEach((item) =>
          laneEvidenceIds.add(item.evidence_id),
        );
      });

      return {
        agentId,
        label: formatNodeLabelById(graph, agentNodeId),
        alertCount: relevantAlerts.filter((alert) => collectAlertAgentIds(alert).includes(agentId))
          .length,
        primaryAlertCount: relevantAlerts.filter(
          (alert) => alert.primary_agent?.agent_id === agentId,
        ).length,
        highlightedEdgeCount: laneEdgeIds.size,
        boundaryCrossingCount: highlightEdges.filter(
          (edge) =>
            (edge.source === agentNodeId || edge.target === agentNodeId) &&
            edge.edge_type === 'PolicyBoundaryCrossing',
        ).length,
        linkedEvidenceCount: laneEvidenceIds.size,
      };
    })
    .sort((left, right) => {
      if (left.primaryAlertCount !== right.primaryAlertCount) {
        return right.primaryAlertCount - left.primaryAlertCount;
      }
      if (left.boundaryCrossingCount !== right.boundaryCrossingCount) {
        return right.boundaryCrossingCount - left.boundaryCrossingCount;
      }
      if (left.linkedEvidenceCount !== right.linkedEvidenceCount) {
        return right.linkedEvidenceCount - left.linkedEvidenceCount;
      }
      if (left.highlightedEdgeCount !== right.highlightedEdgeCount) {
        return right.highlightedEdgeCount - left.highlightedEdgeCount;
      }
      return left.agentId.localeCompare(right.agentId);
    });
  const boundaryCrossingCount = highlightEdges.filter(
    (edge) => edge.edge_type === 'PolicyBoundaryCrossing',
  ).length;
  const delegationEdgeCount = highlightEdges.filter(
    (edge) => edge.edge_type === 'AgentDelegatesTo',
  ).length;
  const pressureScore = Math.max(
    10,
    Math.min(
      100,
      primaryAlerts.length * 24 +
        supportingAlerts.length * 8 +
        boundaryCrossingCount * 24 +
        delegationEdgeCount * 14 +
        highlightEdgeIds.length * 8 +
        surfacedResources.length * 6 +
        collectLinkedEvidence(relevantAlerts, evidence, directNode?.evidence_refs ?? []).length *
          10 +
        severityBoost(highestSeverity),
    ),
  );

  return {
    clusterId,
    title: directNode ? formatNodeLabelById(graph, directNode.node_id) : clusterId,
    summary:
      anchorAlerts[0]?.summary ??
      '当前尚未发现与该集群直接绑定的告警，后续接入真实 cluster 识别后这里会显示成熟监测摘要。',
    alertIds: relevantAlerts.map((alert) => alert.alert_id),
    primaryAlertIds: primaryAlerts.map((alert) => alert.alert_id),
    supportingAlertIds: supportingAlerts.map((alert) => alert.alert_id),
    leadAlertId: anchorAlerts[0]?.alert_id ?? null,
    leadAgentId: agentLanes[0]?.agentId ?? null,
    relatedAgentIds,
    relatedAgentLabels: relatedAgentIds.map((agentId) => formatNodeLabelById(graph, agentId)),
    agentLanes,
    highlightEdgeIds,
    linkedEvidence: collectLinkedEvidence(
      relevantAlerts,
      evidence,
      directNode?.evidence_refs ?? [],
    ),
    surfacedResources,
    resourceCounts,
    boundaryCrossingCount,
    delegationEdgeCount,
    highestSeverity,
    pressureScore,
  };
}

// ── Bounded cluster modeling ──
//
// A full ClusterModel costs O(graph) per cluster to build (edge scans,
// tracebacks, resource surfacing). On a large graph — where every agent is its
// own cluster, so thousands of them — modeling EVERY cluster stalls the main
// thread for seconds and defeats the dashboard's skeleton-first loading. So we
// rank all clusters with cheap signals (alert association + connectivity
// degree, both computed in one pass) and deep-model only the top MAX.
// Alert-referenced clusters always rank at the top, so the selected incident
// remains findable in the top set.
const MAX_MODELED_CLUSTERS = 16;

const cheapSeverityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Info: 1,
  Warning: 2,
  Critical: 3,
};

export function buildClusterModels(
  graph: GraphData,
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[] = [],
): ClusterModel[] {
  const clusterIds = deriveClusterIds(alerts, graph);
  if (clusterIds.length === 0) return [];
  if (clusterIds.length <= MAX_MODELED_CLUSTERS) {
    return clusterIds
      .map((id) => buildClusterModel(id, graph, alerts, evidence))
      .sort(compareClusterModels);
  }

  // agent_id → node_id (agent nodes only)
  const nodeIdByAgent = new Map<string, string>();
  for (const node of graph.nodes) {
    if (node.node_type === 'Agent' && node.agent_id) {
      nodeIdByAgent.set(node.agent_id, node.node_id);
    }
  }

  // node_id → incident-edge count (single pass over edges)
  const degree = new Map<string, number>();
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }

  // alert → referenced agent ids (collected ONCE, not per cluster)
  const alertAgents = alerts.map((alert) => new Set(collectAlertAgentIds(alert)));

  const scored = clusterIds.map((clusterId) => {
    let alertCount = 0;
    let maxSeverity = 0;
    for (let i = 0; i < alerts.length; i++) {
      if (alertAgents[i].has(clusterId)) {
        alertCount += 1;
        maxSeverity = Math.max(maxSeverity, cheapSeverityWeight[alerts[i].severity] ?? 0);
      }
    }
    const nodeId = nodeIdByAgent.get(clusterId);
    const degreeScore = nodeId ? (degree.get(nodeId) ?? 0) : 0;
    return { clusterId, score: alertCount * 10000 + maxSeverity * 1000 + degreeScore };
  });

  scored.sort((a, b) => b.score - a.score);

  return scored
    .slice(0, MAX_MODELED_CLUSTERS)
    .map(({ clusterId }) => buildClusterModel(clusterId, graph, alerts, evidence))
    .sort(compareClusterModels);
}
