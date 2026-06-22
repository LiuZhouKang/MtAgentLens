import type { GraphData, ResponsibilityAlert } from './types';

export function deriveAgentIdsFromAlertsAndGraph(alerts: ResponsibilityAlert[], graph: GraphData): string[] {
  const alertAgentIds = alerts.flatMap((alert) => [
    alert.primary_agent?.agent_id,
    ...alert.involved_agents.map((agent) => agent.agent_id),
  ]);
  const graphAgentIds = graph.nodes
    .filter((node) => node.node_type === 'Agent')
    .map((node) => node.agent_id ?? node.node_id);

  return Array.from(new Set([...alertAgentIds, ...graphAgentIds].filter((value): value is string => !!value)));
}

export function deriveClusterIds(alerts: ResponsibilityAlert[], graph: GraphData): string[] {
  return deriveAgentIdsFromAlertsAndGraph(alerts, graph);
}
