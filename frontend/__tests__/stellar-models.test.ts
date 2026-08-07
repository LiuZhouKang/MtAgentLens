import { describe, expect, it } from 'vitest';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';
import { buildClusterModel, compareClusterModels } from '@/lib/cluster-model';
import { buildAgentModel } from '@/lib/agent-model';

describe('stellar model builders', () => {
  it('builds a cluster model from graph, alerts and evidence', () => {
    const model = buildClusterModel('agent-security', demoGraph, demoAlerts, demoEvidence);

    expect(model.clusterId).toBe('agent-security');
    expect(model.alertIds).toEqual(['demo-alert-1']);
    expect(model.primaryAlertIds).toEqual(['demo-alert-1']);
    expect(model.supportingAlertIds).toEqual([]);
    expect(model.leadAgentId).toBe('agent-security');
    expect(model.relatedAgentIds).toEqual(
      expect.arrayContaining(['agent-security', 'agent-frontend']),
    );
    expect(model.highlightEdgeIds).toEqual(
      expect.arrayContaining(['e-delegate', 'e-taint', 'e-boundary']),
    );
    expect(model.linkedEvidence.map((item) => item.evidence_id)).toEqual(['ev-3', 'ev-4']);
    expect(model.surfacedResources).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: 'boundary-system', bucket: 'boundaries' }),
        expect.objectContaining({ label: '/etc/shadow', bucket: 'files' }),
      ]),
    );
    expect(model.resourceCounts.files).toBeGreaterThan(0);
    expect(model.resourceCounts.boundaries).toBeGreaterThan(0);
    expect(model.delegationEdgeCount).toBe(1);
    expect(model.boundaryCrossingCount).toBe(1);
    expect(model.agentLanes[0]).toEqual(
      expect.objectContaining({
        agentId: 'agent-security',
        primaryAlertCount: 1,
        boundaryCrossingCount: 1,
      }),
    );
    expect(model.pressureScore).toBeGreaterThan(0);
  });

  it('builds an agent model with touched resources, related agents and linked evidence', () => {
    const model = buildAgentModel('agent-frontend', demoGraph, demoAlerts, demoEvidence);

    expect(model.agentId).toBe('agent-frontend');
    expect(model.alertIds).toEqual(expect.arrayContaining(['demo-alert-1', 'demo-alert-2']));
    expect(model.relatedAgentIds).toEqual(
      expect.arrayContaining(['agent-security', 'agent-builder']),
    );
    expect(model.touchedResources).toEqual(
      expect.arrayContaining([
        '/src/components/Button.tsx',
        '/package.json',
        'api.anthropic.com:443',
      ]),
    );
    expect(model.surfacedResources).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: '/src/components/Button.tsx' }),
        expect.objectContaining({ label: '/package.json' }),
        expect.objectContaining({ label: 'api.anthropic.com:443' }),
      ]),
    );
    expect(model.surfacedResources.length).toBeLessThanOrEqual(8);
    expect(model.resourceCounts.files).toBeGreaterThan(0);
    expect(model.resourceCounts.networks).toBeGreaterThan(0);
    expect(model.linkedEvidence.map((item) => item.evidence_id)).toEqual(
      expect.arrayContaining(['ev-1', 'ev-2', 'ev-4']),
    );
  });

  it('returns a stable empty cluster model when no matching data is found', () => {
    const model = buildClusterModel('cluster-missing', demoGraph, demoAlerts, demoEvidence);

    expect(model.clusterId).toBe('cluster-missing');
    expect(model.alertIds).toEqual([]);
    expect(model.primaryAlertIds).toEqual([]);
    expect(model.supportingAlertIds).toEqual([]);
    expect(model.relatedAgentIds).toEqual([]);
    expect(model.leadAgentId).toBeNull();
    expect(model.linkedEvidence).toEqual([]);
    expect(model.surfacedResources).toEqual([]);
    expect(model.resourceCounts).toEqual({
      files: 0,
      networks: 0,
      tools: 0,
      processes: 0,
      contexts: 0,
      boundaries: 0,
      other: 0,
    });
  });

  it('sorts cluster models by pressure before lower-signal anchors', () => {
    const models = ['agent-builder', 'agent-frontend', 'agent-security']
      .map((clusterId) => buildClusterModel(clusterId, demoGraph, demoAlerts, demoEvidence))
      .sort(compareClusterModels);

    expect(models.map((model) => model.clusterId)).toEqual([
      'agent-security',
      'agent-frontend',
      'agent-builder',
    ]);
  });

  it('returns a stable empty agent model when no matching data is found', () => {
    const model = buildAgentModel('agent-missing', demoGraph, demoAlerts, demoEvidence);

    expect(model.agentId).toBe('agent-missing');
    expect(model.alertIds).toEqual([]);
    expect(model.relatedAgentIds).toEqual([]);
    expect(model.touchedResources).toEqual([]);
    expect(model.surfacedResources).toEqual([]);
    expect(model.resourceCounts).toEqual({
      files: 0,
      networks: 0,
      tools: 0,
      processes: 0,
      contexts: 0,
      boundaries: 0,
      other: 0,
    });
    expect(model.linkedEvidence).toEqual([]);
  });
});
