// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { LiveAlertFeed, pickRecentAlerts } from '@/components/live/live-alert-feed';
import { useAppStore } from '@/lib/store';
import type { ResponsibilityAlert } from '@/lib/types';

vi.mock('@/lib/api', () => ({
  api: { getRuns: vi.fn().mockResolvedValue([]) },
}));

function makeAlert(id: string, title: string, severity: ResponsibilityAlert['severity'] = 'Critical') {
  return {
    alert_id: id,
    timestamp_ns: 0,
    severity,
    title,
    summary: `${title} 摘要`,
    confidence: 0.9,
    decision: 'Unsafe' as const,
    authority: 'Authoritative' as const,
    display_profile: 'UnsafeByEngine' as const,
    reasoning: '',
    action_hint: null,
    primary_agent: { agent_id: 'ag1', claimed_role: null },
    involved_agents: [],
    engine: {
      rule_verdicts: [],
      pattern_verdicts: [],
      classification_score: 0,
      classification_route: '',
      score_breakdown: {
        rule_score: 0,
        pattern_score: 0,
        boundary_score: 0,
        evidence_score: 0,
        role_score: 0,
        repetition_score: 0,
        behavioral_score: 0,
        total: 0,
      },
    },
    timeline: { steps: [], total_duration_ms: 0 },
    graph_snapshot: { snapshot_id: '', timestamp_ns: 0, nodes: [], edges: [], metrics: {} },
    phase: { Provisional: { timestamp_ns: 0 } },
    provisional_ref: null,
  } as unknown as ResponsibilityAlert;
}

describe('LiveAlertFeed —— 实时告警流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({
      appState: 'replay',
      alerts: [],
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('渲染最近告警（title + 置信度）', () => {
    const a1 = makeAlert('a1', '污染源判定');
    const a2 = { ...makeAlert('a2', '委派越界'), confidence: 0.7 };
    useAppStore.setState({ alerts: [a1, a2] });
    render(<LiveAlertFeed />);
    expect(screen.getByText('污染源判定')).toBeTruthy();
    expect(screen.getByText('委派越界')).toBeTruthy();
    expect(screen.getByText('90%')).toBeTruthy();
    expect(screen.getByText('70%')).toBeTruthy();
  });

  it('live 态显示「● 实时」徽章', () => {
    useAppStore.setState({
      appState: 'live',
      alerts: [makeAlert('a1', '污染源判定')],
    });
    render(<LiveAlertFeed />);
    expect(screen.getByTestId('live-badge')).toBeTruthy();
  });

  it('replay 态不显示实时徽章', () => {
    useAppStore.setState({ alerts: [makeAlert('a1', '污染源判定')] });
    render(<LiveAlertFeed />);
    expect(screen.queryByTestId('live-badge')).toBeNull();
  });

  it('点击告警 → focusAlert 高亮图路径', () => {
    const focusSpy = vi.spyOn(useAppStore.getState(), 'focusAlert');
    useAppStore.setState({ alerts: [makeAlert('a1', '污染源判定')] });
    render(<LiveAlertFeed />);
    screen.getByText('污染源判定').click();
    expect(focusSpy).toHaveBeenCalledWith(expect.objectContaining({ alert_id: 'a1' }));
  });

  it('空态提示', () => {
    useAppStore.setState({ alerts: [] });
    render(<LiveAlertFeed />);
    expect(screen.getByText(/暂无告警/)).toBeTruthy();
  });

  it('pickRecentAlerts 截取最近 N 条', () => {
    const alerts = [makeAlert('a1', 'A'), makeAlert('a2', 'B'), makeAlert('a3', 'C')];
    expect(pickRecentAlerts(alerts, 2).map((a) => a.alert_id)).toEqual(['a1', 'a2']);
  });
});
