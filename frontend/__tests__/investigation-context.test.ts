import { describe, expect, it } from 'vitest';
import { demoAlerts } from '@/lib/demo-data';
import {
  DEFAULT_INVESTIGATION_CONTEXT,
  buildInvestigationContextFromAlert,
  mergeInvestigationContext,
} from '@/lib/investigation-context';

describe('investigation context', () => {
  it('derives a shared drilldown focus from an alert', () => {
    const alert = demoAlerts[0];

    const context = buildInvestigationContextFromAlert(alert);

    expect(context.activeIncidentId).toBe(alert.alert_id);
    expect(context.activeAgentId).toBe(alert.primary_agent.agent_id);
    expect(context.highlightedPathEdgeIds).toEqual(
      alert.graph_snapshot.edges.map((edge) => edge.edge_id),
    );
    expect(context.graphFocus.reason).toBe('incident');
    expect(context.graphFocus.label).toContain(alert.title);
  });

  it('merges partial focus updates without dropping the current incident', () => {
    const alertContext = buildInvestigationContextFromAlert(demoAlerts[0]);

    const next = mergeInvestigationContext(alertContext, {
      activeEvidenceId: 'evidence-1',
      graphFocus: {
        ...alertContext.graphFocus,
        reason: 'evidence',
        label: 'evidence-1',
      },
    });

    expect(next.activeIncidentId).toBe(alertContext.activeIncidentId);
    expect(next.activeEvidenceId).toBe('evidence-1');
    expect(next.graphFocus.reason).toBe('evidence');
  });

  it('keeps a production live default instead of presentation semantics', () => {
    expect(DEFAULT_INVESTIGATION_CONTEXT.mode).toBe('live');
    expect(DEFAULT_INVESTIGATION_CONTEXT.graphFocus.reason).toBe('overview');
  });
});
