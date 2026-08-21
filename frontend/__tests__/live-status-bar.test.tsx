// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { LiveStatusBar } from '@/components/live/live-status-bar';
import { DEFAULT_LIVE_STATE } from '@/lib/live/live-types';
import { useAppStore } from '@/lib/store';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}));
vi.mock('@/lib/api', () => ({
  api: { getRuns: vi.fn().mockResolvedValue([]) },
}));

describe('LiveStatusBar —— 实时连接状态常驻栏', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({
      appState: 'replay',
      live: DEFAULT_LIVE_STATE,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('idle：显示「实时未启用」', () => {
    render(<LiveStatusBar />);
    expect(screen.getByText('实时未启用')).toBeTruthy();
  });

  it('connected：显示「实时」+ 活跃 run + 时长 + 速率', () => {
    useAppStore.getState().setLiveStatus('connected');
    useAppStore
      .getState()
      .setLiveRun({ runId: 'run-abc-1234', startedAtNs: Date.now() * 1e6 - 83e9, endedAtNs: null, isActive: true });
    useAppStore.getState().setLiveStats({ eventRate: 12 });
    render(<LiveStatusBar />);
    expect(screen.getByText('实时')).toBeTruthy();
    expect(screen.getByText('run-abc-')).toBeTruthy(); // runId 截断前 8 位
    expect(screen.getByText('1m23s')).toBeTruthy();
    expect(screen.getByText('12/s')).toBeTruthy();
  });

  it('reconnecting：显示「重连中」', () => {
    useAppStore.getState().setLiveStatus('reconnecting');
    render(<LiveStatusBar />);
    expect(screen.getByText('重连中')).toBeTruthy();
  });

  it('disconnected + lag：显示「实时断开」与丢弃角标', () => {
    useAppStore.getState().setLiveStatus('disconnected');
    useAppStore.getState().setLiveStats({ droppedCount: 3 });
    render(<LiveStatusBar />);
    expect(screen.getByText('实时断开')).toBeTruthy();
    expect(screen.getByText('↯3')).toBeTruthy();
  });

  it('点击跳转态势页', () => {
    render(<LiveStatusBar />);
    screen.getByTestId('live-status-bar').click();
    expect(push).toHaveBeenCalledWith('/console');
  });
});
