// Deep-link focus for the overview dashboard (/overview).
//
// Both the old agent (主体) and unit (拓扑) affordances were consolidated
// into the overview dashboard. They deep-link here via URL fragments:
//   /overview#agentId=<id>     — focus the first alert owned by that agent
//   /overview#unitId=<id>   — focus that responsibility unit's lead alert
// The overview page resolves these to a selected alert, which already drives
// the whole focus chain (unit → graph focus → agent context).

import { parseAgentId } from './agent-route';
import { parseUnitId } from './responsibility-unit-route';

export type OverviewFocus = {
  agentId: string | null;
  unitId: string | null;
};

export function parseOverviewFocus(
  searchParams: URLSearchParams,
  hash?: string | null,
): OverviewFocus {
  return {
    agentId: parseAgentId(searchParams, hash),
    unitId: parseUnitId(searchParams, hash),
  };
}

type FocusAlertLike = {
  alert: { alert_id: string; primary_agent: { agent_id: string } };
};

type FocusUnitLike = {
  unitId: string;
  leadAlertId?: string | null;
};

// Resolve a focus to the alert the overview page should select. Returns null
// when nothing matches yet — callers re-run once alert/unit data lands.
export function resolveOverviewFocus(
  focus: OverviewFocus,
  alertModels: FocusAlertLike[],
  responsibilityUnits: FocusUnitLike[],
): string | null {
  if (focus.agentId) {
    const match = alertModels.find((item) => item.alert.primary_agent.agent_id === focus.agentId);
    return match?.alert.alert_id ?? null;
  }

  if (focus.unitId) {
    const unit = responsibilityUnits.find((model) => model.unitId === focus.unitId);
    return unit?.leadAlertId ?? null;
  }

  return null;
}
