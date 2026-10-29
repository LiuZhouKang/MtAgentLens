import type { GraphData, ResponsibilityAlert } from './types';
import { deriveTracebackEdgeIds } from './traceback';

export type InvestigationMode = 'live' | 'replay' | 'presentation';
export type GraphFocusReason =
  'overview' | 'incident' | 'agent' | 'unit' | 'edge' | 'resource' | 'evidence' | 'handoff';

export type InvestigationGraphFocus = {
  reason: GraphFocusReason;
  label: string;
  nodeId: string | null;
  edgeId: string | null;
  agentId: string | null;
};

export type InvestigationContext = {
  mode: InvestigationMode;
  activeIncidentId: string | null;
  activeAgentId: string | null;
  activeUnitId: string | null;
  activeNodeId: string | null;
  activeEdgeId: string | null;
  activeResourceId: string | null;
  activeEvidenceId: string | null;
  highlightedPathEdgeIds: string[];
  severityFilter: Array<'Info' | 'Warning' | 'Critical'>;
  confidenceThreshold: number;
  graphFocus: InvestigationGraphFocus;
};

export const DEFAULT_INVESTIGATION_CONTEXT: InvestigationContext = {
  mode: 'live',
  activeIncidentId: null,
  activeAgentId: null,
  activeUnitId: null,
  activeNodeId: null,
  activeEdgeId: null,
  activeResourceId: null,
  activeEvidenceId: null,
  highlightedPathEdgeIds: [],
  severityFilter: ['Warning', 'Critical'],
  confidenceThreshold: 0,
  graphFocus: {
    reason: 'overview',
    label: 'Live responsibility field',
    nodeId: null,
    edgeId: null,
    agentId: null,
  },
};

export function buildInvestigationContextFromAlert(
  alert: ResponsibilityAlert,
  fallbackGraph?: GraphData,
): InvestigationContext {
  const snapshot = alert.graph_snapshot?.nodes?.length ? alert.graph_snapshot : fallbackGraph;
  const highlightedPathEdgeIds = deriveTracebackEdgeIds(alert, snapshot?.edges ?? []);
  const agentId = alert.primary_agent?.agent_id ?? null;
  const primaryNode = snapshot?.nodes.find((node) => node.agent_id === agentId);

  return {
    ...DEFAULT_INVESTIGATION_CONTEXT,
    mode: 'live',
    activeIncidentId: alert.alert_id,
    activeAgentId: agentId,
    activeNodeId: primaryNode?.node_id ?? null,
    activeEvidenceId:
      alert.timeline?.steps?.find((step) => step.evidence_ref)?.evidence_ref ?? null,
    highlightedPathEdgeIds,
    graphFocus: {
      reason: 'incident',
      label: `${alert.title} · ${alert.severity}`,
      nodeId: primaryNode?.node_id ?? null,
      edgeId: highlightedPathEdgeIds[0] ?? null,
      agentId,
    },
  };
}

export function mergeInvestigationContext(
  current: InvestigationContext,
  patch: Partial<InvestigationContext>,
): InvestigationContext {
  return {
    ...current,
    ...patch,
    highlightedPathEdgeIds: patch.highlightedPathEdgeIds ?? current.highlightedPathEdgeIds,
    severityFilter: patch.severityFilter ?? current.severityFilter,
    graphFocus: {
      ...current.graphFocus,
      ...patch.graphFocus,
    },
  };
}
