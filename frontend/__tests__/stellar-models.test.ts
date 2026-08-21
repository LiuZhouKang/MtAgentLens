import { describe, expect, it } from 'vitest';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';
import { buildResponsibilityUnit, compareResponsibilityUnits } from '@/lib/responsibility-unit';

describe('stellar model builders', () => {
  it('builds a responsibility unit from graph, alerts and evidence', () => {
    const model = buildResponsibilityUnit('agent-security', demoGraph, demoAlerts, demoEvidence);

    expect(model.unitId).toBe('agent-security');
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

  it('returns a stable empty responsibility unit when no matching data is found', () => {
    const model = buildResponsibilityUnit('unit-missing', demoGraph, demoAlerts, demoEvidence);

    expect(model.unitId).toBe('unit-missing');
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

  it('sorts responsibility units by pressure before lower-signal anchors', () => {
    const models = ['agent-builder', 'agent-frontend', 'agent-security']
      .map((unitId) => buildResponsibilityUnit(unitId, demoGraph, demoAlerts, demoEvidence))
      .sort(compareResponsibilityUnits);

    expect(models.map((model) => model.unitId)).toEqual([
      'agent-security',
      'agent-frontend',
      'agent-builder',
    ]);
  });
});
