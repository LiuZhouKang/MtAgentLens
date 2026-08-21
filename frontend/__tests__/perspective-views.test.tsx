// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { renderPerspectiveView } from '@/components/overview/monitoring/perspective-views';
import type {
  MonitoringState,
  PerspectiveHealth,
  PerspectiveKey,
} from '@/lib/monitoring/monitoring-types';

const KEYS: PerspectiveKey[] = [
  'topologyDrift',
  'topologyHealth',
  'intentMismatch',
  'policyCompliance',
  'toolFootprint',
  'networkSessions',
];

function health(
  over: Partial<Record<PerspectiveKey, Partial<PerspectiveHealth>>> = {},
): MonitoringState['health'] {
  const base: PerspectiveHealth = { status: 'normal', available: true, score: 0, signals: ['—'] };
  const out = {} as MonitoringState['health'];
  for (const k of KEYS) out[k] = { ...base, ...(over[k] ?? {}) };
  return out;
}

function makeMonitoring(over: Partial<MonitoringState> = {}): MonitoringState {
  return {
    derivedAtMs: 0,
    sourceLabel: 'run-42.db',
    health: health(over.health),
    topologyDrift: null,
    topologyHealth: null,
    intentMismatch: null,
    policyCompliance: null,
    toolFootprint: null,
    networkSessions: null,
    ...over,
  };
}

describe('PerspectiveView — 六个视角详情', () => {
  it('拓扑漂移视图展示帧线图与首末差分', () => {
    const monitoring = makeMonitoring({
      topologyDrift: {
        frames: [
          {
            snapshot_id: 'f0',
            timestamp_ns: 0,
            nodeCount: 2,
            edgeCount: 2,
            agentCount: 1,
            crossAgentEdges: 0,
            delegationDepth: 0,
            taintChains: 0,
            avgConfidence: 0.6,
          },
          {
            snapshot_id: 'f1',
            timestamp_ns: 1,
            nodeCount: 3,
            edgeCount: 2,
            agentCount: 1,
            crossAgentEdges: 0,
            delegationDepth: 0,
            taintChains: 0,
            avgConfidence: 0.85,
          },
        ],
        latest: null,
        delta: { nodes: 1, edges: 0, agents: 0 },
      },
    });
    render(<>{renderPerspectiveView('topologyDrift', monitoring, () => {})}</>);
    expect(screen.getByText(/Δ节点 \+1/)).toBeTruthy();
  });

  it('拓扑健康视图展示污染源 agent，点击走 focusAgent', () => {
    const onFocus = vi.fn();
    const monitoring = makeMonitoring({
      topologyHealth: {
        latest: null,
        alertSeverity: { critical: 0, warning: 1, info: 1 },
        pollutionCount: 2,
        pollutionBySource: { 'agent-a': 1, 'agent-b': 1 },
      },
    });
    render(<>{renderPerspectiveView('topologyHealth', monitoring, onFocus)}</>);
    expect(screen.getByRole('button', { name: /agent-a/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /agent-b/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /agent-a/ }));
    expect(onFocus).toHaveBeenCalledWith('agent-a');
  });
});
