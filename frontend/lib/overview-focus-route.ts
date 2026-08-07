// Deep-link focus for the overview dashboard (/overview).
//
// Both the old agent (主体) and cluster (拓扑) affordances were consolidated
// into the overview dashboard. They deep-link here via URL fragments:
//   /overview#agentId=<id>     — focus the first alert owned by that agent
//   /overview#clusterId=<id>   — focus that responsibility cluster's lead alert
// The overview page resolves these to a selected alert, which already drives
// the whole focus chain (cluster → graph focus → agent context).

import { parseAgentId } from './agent-route';
import { parseClusterId } from './cluster-route';

export type OverviewFocus = {
  agentId: string | null;
  clusterId: string | null;
};

export function parseOverviewFocus(
  searchParams: URLSearchParams,
  hash?: string | null,
): OverviewFocus {
  return {
    agentId: parseAgentId(searchParams, hash),
    clusterId: parseClusterId(searchParams, hash),
  };
}

type FocusAlertLike = {
  alert: { alert_id: string; primary_agent: { agent_id: string } };
};

type FocusClusterLike = {
  clusterId: string;
  leadAlertId?: string | null;
};

// Resolve a focus to the alert the overview page should select. Returns null
// when nothing matches yet — callers re-run once alert/cluster data lands.
export function resolveOverviewFocus(
  focus: OverviewFocus,
  alertModels: FocusAlertLike[],
  clusterModels: FocusClusterLike[],
): string | null {
  if (focus.agentId) {
    const match = alertModels.find(
      (item) => item.alert.primary_agent.agent_id === focus.agentId,
    );
    return match?.alert.alert_id ?? null;
  }

  if (focus.clusterId) {
    const cluster = clusterModels.find((model) => model.clusterId === focus.clusterId);
    return cluster?.leadAlertId ?? null;
  }

  return null;
}
