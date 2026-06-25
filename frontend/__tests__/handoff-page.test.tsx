// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import HandoffPage from '@/app/handoff/page';
import { useHandoffStore } from '@/lib/handoff-store';
import { getDemoDelegationTreeResponse, demoDependencyEdges, demoEvents, demoMetrics } from '@/lib/handoff-demo-data';

vi.mock('@/components/ui/theme-toggle', () => ({
  default: function MockThemeToggle() {
    return <button type="button">主题切换</button>;
  },
}));

describe('HandoffPage', () => {
  beforeEach(() => {
    useHandoffStore.setState({
      delegationTree: getDemoDelegationTreeResponse().tree,
      metrics: demoMetrics,
      recentEvents: demoEvents,
      dependencyEdges: demoDependencyEdges,
      selectedNodeId: null,
      loading: false,
      error: null,
      useBackend: false,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('surfaces the handoff command deck, live feed, and verdict cockpit', () => {
    render(<HandoffPage />);

    expect(screen.getAllByText('planner-agent 与 security-auditor 之间出现内容碰撞').length).toBeGreaterThan(0);
    expect(screen.getByText('planner-agent 主导当前 handoff lane')).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Live Handoff Feed' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Delegation Chain' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Handoff Risk & Verdict' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Quick Actions' })).toBeDefined();
  });
});
