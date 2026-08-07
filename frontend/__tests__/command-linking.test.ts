import { describe, expect, it } from 'vitest';
import { demoAlerts } from '@/lib/demo-data';
import { resolveCommandSelectionContext } from '@/lib/command-linking';
import { buildTraceHref, parseTraceAlertId } from '@/lib/trace-route';

describe('command linking', () => {
  it('prefers agent cockpit when an agent node is selected', () => {
    const context = resolveCommandSelectionContext({
      alerts: demoAlerts,
      featuredAlert: demoAlerts[0],
      selectedStageNode: {
        nodeId: 'agent-security',
        nodeType: 'Agent',
        label: 'security-auditor',
        description: 'selected agent',
        agentId: 'agent-security',
        clusterId: 'cluster-security-anchor',
      },
      selectedStageEdge: null,
    });

    expect(context.alertId).toBe('demo-alert-1');
    expect(context.agentId).toBe('agent-security');
    expect(context.clusterId).toBe('cluster-security-anchor');
    expect(context.surfaceHref).toBe('/overview#agentId=agent-security');
    expect(context.surfaceLabel).toBe('打开主体');
    expect(context.investigationHref).toBe('/investigations#alertId=demo-alert-1');
    expect(context.traceHref).toBe('/traces#alertId=demo-alert-1');
  });

  it('matches boundary crossing edges back to the owning alert', () => {
    const context = resolveCommandSelectionContext({
      alerts: demoAlerts,
      featuredAlert: demoAlerts[1],
      selectedStageNode: null,
      selectedStageEdge: {
        edgeId: 'e-boundary',
        edgeType: 'PolicyBoundaryCrossing',
        sourceId: 'agent-security',
        targetId: 'boundary-system',
        sourceLabel: 'security-auditor',
        targetLabel: 'system boundary',
        sourceType: 'Agent',
        targetType: 'PolicyBoundary',
        confidence: 0.89,
        boundaryKey: 'boundary-system',
      },
    });

    expect(context.alertId).toBe('demo-alert-1');
    expect(context.boundaryKey).toBe('boundary-system');
    expect(context.surfaceHref).toBe('/investigations#alertId=demo-alert-1');
    expect(context.surfaceLabel).toBe('打开主调查');
    expect(context.traceHref).toBe('/traces#alertId=demo-alert-1');
  });

  it('falls back to overview when there is no active selection or alert', () => {
    const context = resolveCommandSelectionContext({
      alerts: [],
      featuredAlert: null,
      selectedStageNode: null,
      selectedStageEdge: null,
    });

    expect(context.alertId).toBeNull();
    expect(context.surfaceHref).toBe('/overview');
    expect(context.surfaceLabel).toBe('打开仪表盘');
    expect(context.traceHref).toBe('/traces');
  });
});

describe('trace route', () => {
  it('builds and parses alert deep links', () => {
    expect(buildTraceHref('demo-alert-1')).toBe('/traces#alertId=demo-alert-1');
    expect(parseTraceAlertId(new URLSearchParams(), '#alertId=demo-alert-1')).toBe('demo-alert-1');
  });

  it('prefers query params over hash when parsing', () => {
    const params = new URLSearchParams('alertId=query-alert');
    expect(parseTraceAlertId(params, '#alertId=hash-alert')).toBe('query-alert');
  });
});
