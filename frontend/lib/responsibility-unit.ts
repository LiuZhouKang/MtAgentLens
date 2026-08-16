import { deriveTracebackEdgeIds } from './traceback';
import { deriveResponsibilityUnitIds } from './stellar-directory';
import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from './types';
import {
  collectAlertAgentIds,
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

export interface UnitAgentLane {
  agentId: string;
  label: string;
  alertCount: number;
  primaryAlertCount: number;
  highlightedEdgeCount: number;
  boundaryCrossingCount: number;
  linkedEvidenceCount: number;
}

export interface ResponsibilityUnit {
  unitId: string;
  title: string;
  summary: string;
  alertIds: string[];
  primaryAlertIds: string[];
  supportingAlertIds: string[];
  leadAlertId: string | null;
  leadAgentId: string | null;
  relatedAgentIds: string[];
  relatedAgentLabels: string[];
  agentLanes: UnitAgentLane[];
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

export function compareResponsibilityUnits(left: ResponsibilityUnit, right: ResponsibilityUnit) {
  if (left.pressureScore !== right.pressureScore) {
    return right.pressureScore - left.pressureScore;
  }

  const leftSelfAnchored = left.leadAgentId === left.unitId ? 1 : 0;
  const rightSelfAnchored = right.leadAgentId === right.unitId ? 1 : 0;
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

  return left.unitId.localeCompare(right.unitId);
}

export function buildResponsibilityUnit(
  unitId: string,
  graph: GraphData,
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[] = [],
): ResponsibilityUnit {
  const relevantAlerts = alerts.filter((alert) => collectAlertAgentIds(alert).includes(unitId));
  const primaryAlerts = relevantAlerts.filter(
    (alert) => alert.primary_agent?.agent_id === unitId,
  );
  const supportingAlerts = relevantAlerts.filter(
    (alert) => alert.primary_agent?.agent_id !== unitId,
  );
  const anchorAlerts = (primaryAlerts.length ? primaryAlerts : relevantAlerts)
    .slice()
    .sort(compareAlerts);
  const directNode = findAgentNode(graph, unitId);
  const connectedNodeIds = directNode
    ? findDirectlyConnectedNodeIds(graph, [directNode.node_id])
    : [];
  const relatedAgentIds = Array.from(
    new Set(
      [
        ...(directNode || relevantAlerts.length ? [unitId] : []),
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

        // 完整证据关联通道：告警 timeline.evidence_ref + 该 agent 相关边的 evidence_refs
        for (const step of alert.timeline.steps) {
          if (step.evidence_ref) laneEvidenceIds.add(step.evidence_ref);
        }
      });
      for (const edge of highlightEdges) {
        if (edge.source === agentNodeId || edge.target === agentNodeId) {
          for (const ref of edge.evidence_refs ?? []) laneEvidenceIds.add(ref);
        }
      }

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
  // 证据关联（与 investigation 模型一致的完整双通道）：告警 timeline.evidence_ref +
  // 相关图边 edge.evidence_refs + 节点 evidence_refs。真实 db 的证据通过图边
  // evidence_refs 关联（3300 边中 3262 带 refs，全匹配 correlation_evidence）——
  // 仅用 timeline.ref（真实 db 告警为空）会漏关联，导致「证据分布」面板误显 0。
  const linkedRefs = new Set<string>(directNode?.evidence_refs ?? []);
  for (const alert of relevantAlerts) {
    for (const step of alert.timeline.steps) {
      if (step.evidence_ref) linkedRefs.add(step.evidence_ref);
    }
  }
  for (const edge of highlightEdges) {
    for (const ref of edge.evidence_refs ?? []) linkedRefs.add(ref);
  }
  const linkedEvidence = evidence.filter((item) => linkedRefs.has(item.evidence_id));
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
        linkedEvidence.length * 10 +
        severityBoost(highestSeverity),
    ),
  );

  return {
    unitId,
    title: directNode ? formatNodeLabelById(graph, directNode.node_id) : unitId,
    summary:
      anchorAlerts[0]?.summary ??
      '当前尚未发现与该责任单元直接绑定的告警，后续接入真实单元识别后这里会显示成熟监测摘要。',
    alertIds: relevantAlerts.map((alert) => alert.alert_id),
    primaryAlertIds: primaryAlerts.map((alert) => alert.alert_id),
    supportingAlertIds: supportingAlerts.map((alert) => alert.alert_id),
    leadAlertId: anchorAlerts[0]?.alert_id ?? null,
    leadAgentId: agentLanes[0]?.agentId ?? null,
    relatedAgentIds,
    relatedAgentLabels: relatedAgentIds.map((agentId) => formatNodeLabelById(graph, agentId)),
    agentLanes,
    highlightEdgeIds,
    linkedEvidence,
    surfacedResources,
    resourceCounts,
    boundaryCrossingCount,
    delegationEdgeCount,
    highestSeverity,
    pressureScore,
  };
}

// ── Bounded responsibility-unit modeling ──
//
// A full ResponsibilityUnit costs O(graph) per responsibility unit to build (edge scans,
// tracebacks, resource surfacing). On a large graph — where every agent is its
// own responsibility unit, so thousands of them — modeling EVERY unit stalls the main
// thread for seconds and defeats the dashboard's skeleton-first loading. So we
// rank all units with cheap signals (alert association + connectivity
// degree, both computed in one pass) and deep-model only the top MAX.
// Alert-referenced units always rank at the top, so the selected incident
// remains findable in the top set.
const MAX_MODELED_CLUSTERS = 16;

const cheapSeverityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Info: 1,
  Warning: 2,
  Critical: 3,
};

export function buildResponsibilityUnits(
  graph: GraphData,
  alerts: ResponsibilityAlert[],
  evidence: CorrelationEvidence[] = [],
): ResponsibilityUnit[] {
  const unitIds = deriveResponsibilityUnitIds(alerts, graph);
  if (unitIds.length === 0) return [];
  if (unitIds.length <= MAX_MODELED_CLUSTERS) {
    return unitIds
      .map((id) => buildResponsibilityUnit(id, graph, alerts, evidence))
      .sort(compareResponsibilityUnits);
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

  // alert → referenced agent ids (collected ONCE, not per unit)
  const alertAgents = alerts.map((alert) => new Set(collectAlertAgentIds(alert)));

  const scored = unitIds.map((unitId) => {
    let alertCount = 0;
    let maxSeverity = 0;
    for (let i = 0; i < alerts.length; i++) {
      if (alertAgents[i].has(unitId)) {
        alertCount += 1;
        maxSeverity = Math.max(maxSeverity, cheapSeverityWeight[alerts[i].severity] ?? 0);
      }
    }
    const nodeId = nodeIdByAgent.get(unitId);
    const degreeScore = nodeId ? (degree.get(nodeId) ?? 0) : 0;
    return { unitId, score: alertCount * 10000 + maxSeverity * 1000 + degreeScore };
  });

  scored.sort((a, b) => b.score - a.score);

  return scored
    .slice(0, MAX_MODELED_CLUSTERS)
    .map(({ unitId }) => buildResponsibilityUnit(unitId, graph, alerts, evidence))
    .sort(compareResponsibilityUnits);
}
