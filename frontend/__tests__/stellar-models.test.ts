import { describe, expect, it } from 'vitest';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';
import { buildClusterModel } from '@/lib/cluster-model';
import { buildAgentModel } from '@/lib/agent-model';

describe('stellar model builders', () => {
  it('builds a cluster model from graph, alerts and evidence', () => {
    const model = buildClusterModel('agent-security', demoGraph, demoAlerts, demoEvidence);

    expect(model.clusterId).toBe('agent-security');
    expect(model.alertIds).toEqual(['demo-alert-1']);
    expect(model.relatedAgentIds).toEqual(expect.arrayContaining(['agent-security', 'agent-frontend']));
    expect(model.highlightEdgeIds).toEqual(expect.arrayContaining(['e-delegate', 'e-taint', 'e-boundary']));
    expect(model.linkedEvidence.map((item) => item.evidence_id)).toEqual(['ev-3', 'ev-4']);
    expect(model.boundaryCrossingCount).toBe(1);
  });

  it('builds an agent model with touched resources, related agents and linked evidence', () => {
    const model = buildAgentModel('agent-frontend', demoGraph, demoAlerts, demoEvidence);

    expect(model.agentId).toBe('agent-frontend');
    expect(model.alertIds).toEqual(expect.arrayContaining(['demo-alert-1', 'demo-alert-2']));
    expect(model.relatedAgentIds).toEqual(expect.arrayContaining(['agent-security', 'agent-builder']));
    expect(model.touchedResources).toEqual(
      expect.arrayContaining(['/src/components/Button.tsx', '/package.json', 'api.anthropic.com:443']),
    );
    expect(model.linkedEvidence.map((item) => item.evidence_id)).toEqual(expect.arrayContaining(['ev-1', 'ev-2', 'ev-4']));
  });

  it('returns a stable empty cluster model when no matching data is found', () => {
    const model = buildClusterModel('cluster-missing', demoGraph, demoAlerts, demoEvidence);

    expect(model.clusterId).toBe('cluster-missing');
    expect(model.alertIds).toEqual([]);
    expect(model.relatedAgentIds).toEqual([]);
    expect(model.linkedEvidence).toEqual([]);
  });

  it('returns a stable empty agent model when no matching data is found', () => {
    const model = buildAgentModel('agent-missing', demoGraph, demoAlerts, demoEvidence);

    expect(model.agentId).toBe('agent-missing');
    expect(model.alertIds).toEqual([]);
    expect(model.relatedAgentIds).toEqual([]);
    expect(model.touchedResources).toEqual([]);
    expect(model.linkedEvidence).toEqual([]);
  });
});
