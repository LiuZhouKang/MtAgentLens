import type { StageEdgeSelection, StageNodeSelection } from '@/components/ui/home-engine-stage';
import type { ResponsibilityAlert } from '@/lib/types';
import { buildAgentHref } from '@/lib/agent-route';
import { buildClusterHref } from '@/lib/cluster-route';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildTraceHref } from '@/lib/trace-route';

type ResolveCommandSelectionContextInput = {
  alerts: ResponsibilityAlert[];
  featuredAlert: ResponsibilityAlert | null;
  selectedStageNode: StageNodeSelection | null;
  selectedStageEdge: StageEdgeSelection | null;
};

export type CommandSelectionContext = {
  alertId: string | null;
  agentId: string | null;
  clusterId: string | null;
  boundaryKey: string | null;
  surfaceHref: string;
  surfaceLabel: string;
  investigationHref: string;
  traceHref: string;
};

function alertTouchesAgent(alert: ResponsibilityAlert, agentId: string) {
  return (
    alert.primary_agent.agent_id === agentId ||
    alert.involved_agents.some((agent) => agent.agent_id === agentId) ||
    alert.timeline.steps.some((step) => step.agent_id === agentId)
  );
}

function alertTouchesBoundary(alert: ResponsibilityAlert, boundaryKey: string) {
  return (
    alert.timeline.steps.some(
      (step) => step.resource === boundaryKey || step.description.includes(boundaryKey),
    ) ||
    alert.graph_snapshot?.nodes?.some((node) => node.node_id === boundaryKey) ||
    alert.graph_snapshot?.edges?.some(
      (edge) => edge.source === boundaryKey || edge.target === boundaryKey,
    )
  );
}

function alertTouchesEdge(
  alert: ResponsibilityAlert,
  edgeId: string,
  sourceId: string,
  targetId: string,
) {
  return alert.graph_snapshot?.edges?.some(
    (edge) => edge.edge_id === edgeId || (edge.source === sourceId && edge.target === targetId),
  ) ?? false;
}

function resolveRelatedAlert(
  alerts: ResponsibilityAlert[],
  selectedStageNode: StageNodeSelection | null,
  selectedStageEdge: StageEdgeSelection | null,
) {
  const agentId =
    selectedStageNode?.agentId ??
    (selectedStageNode?.nodeType === 'Agent' ? selectedStageNode.nodeId : null) ??
    (selectedStageEdge?.sourceType === 'Agent' ? selectedStageEdge.sourceId : null) ??
    null;

  if (agentId) {
    const agentAlert = alerts.find((alert) => alertTouchesAgent(alert, agentId));
    if (agentAlert) {
      return agentAlert;
    }
  }

  const boundaryKey = selectedStageNode?.boundaryKey ?? selectedStageEdge?.boundaryKey ?? null;
  if (boundaryKey) {
    const boundaryAlert = alerts.find((alert) => alertTouchesBoundary(alert, boundaryKey));
    if (boundaryAlert) {
      return boundaryAlert;
    }
  }

  if (selectedStageEdge) {
    const edgeAlert = alerts.find((alert) =>
      alertTouchesEdge(
        alert,
        selectedStageEdge.edgeId,
        selectedStageEdge.sourceId,
        selectedStageEdge.targetId,
      ),
    );
    if (edgeAlert) {
      return edgeAlert;
    }
  }

  return null;
}

export function resolveCommandSelectionContext({
  alerts,
  featuredAlert,
  selectedStageNode,
  selectedStageEdge,
}: ResolveCommandSelectionContextInput): CommandSelectionContext {
  const hasExplicitEdgeSelection = Boolean(selectedStageEdge);
  const relatedAlert =
    resolveRelatedAlert(alerts, selectedStageNode, selectedStageEdge) ?? featuredAlert ?? null;
  const alertId = relatedAlert?.alert_id ?? null;
  const agentId =
    selectedStageNode?.agentId ??
    (selectedStageNode?.nodeType === 'Agent' ? selectedStageNode.nodeId : null) ??
    (!hasExplicitEdgeSelection && selectedStageEdge?.sourceType === 'Agent'
      ? selectedStageEdge.sourceId
      : null) ??
    relatedAlert?.primary_agent.agent_id ??
    null;
  const clusterId = selectedStageNode?.clusterId ?? null;
  const boundaryKey = selectedStageNode?.boundaryKey ?? selectedStageEdge?.boundaryKey ?? null;

  if (selectedStageNode && agentId) {
    return {
      alertId,
      agentId,
      clusterId,
      boundaryKey,
      surfaceHref: buildAgentHref(agentId),
      surfaceLabel: '打开主体',
      investigationHref: buildInvestigationHref(alertId),
      traceHref: buildTraceHref(alertId),
    };
  }

  if (clusterId) {
    return {
      alertId,
      agentId,
      clusterId,
      boundaryKey,
      surfaceHref: buildClusterHref(clusterId),
      surfaceLabel: '打开责任集群',
      investigationHref: buildInvestigationHref(alertId),
      traceHref: buildTraceHref(alertId),
    };
  }

  if (alertId) {
    return {
      alertId,
      agentId,
      clusterId,
      boundaryKey,
      surfaceHref: buildInvestigationHref(alertId),
      surfaceLabel: '打开主调查',
      investigationHref: buildInvestigationHref(alertId),
      traceHref: buildTraceHref(alertId),
    };
  }

  return {
    alertId: null,
    agentId,
    clusterId,
    boundaryKey,
    surfaceHref: '/overview',
    surfaceLabel: '打开仪表盘',
    investigationHref: buildInvestigationHref(null),
    traceHref: buildTraceHref(null),
  };
}
