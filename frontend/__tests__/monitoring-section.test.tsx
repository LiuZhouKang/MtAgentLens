// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import MonitoringSection from '@/components/overview/monitoring/monitoring-section';
import { useAppStore } from '@/lib/store';
import type { MonitoringState, PerspectiveHealth, PerspectiveKey } from '@/lib/monitoring/monitoring-types';

const KEYS: PerspectiveKey[] = ['topologyDrift', 'topologyHealth', 'intentMismatch', 'policyCompliance', 'toolFootprint', 'networkSessions'];

/** 默认全部 normal/available，可按 key 覆盖——与 PerspectiveHealth 类型严格一致。 */
function health(over: Partial<Record<PerspectiveKey, Partial<PerspectiveHealth>>> = {}): MonitoringState['health'] {
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

afterEach(() => {
  cleanup();
  useAppStore.setState({ monitoring: null });
});

describe('MonitoringSection', () => {
  it('渲染区块头、6 个健康格、实时占位行与 tab', () => {
    useAppStore.setState({ monitoring: makeMonitoring() });
    render(<MonitoringSection />);
    expect(screen.getByText('运行监控')).toBeTruthy();
    for (const label of ['拓扑漂移', '拓扑健康', '意图-动作失配', '策略合规', '工具足迹', '网络会话']) {
      // 每视角出现在健康格 + tab + 卡片各一次
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(/实时视角 · 阶段二/)).toBeTruthy();
    expect(screen.getByText('LLM API 追踪')).toBeTruthy();
    expect(screen.getByRole('button', { name: '全部' })).toBeTruthy();
  });

  it('缺表视角（available=false）显示「无此表」而非并入健康聚合', () => {
    useAppStore.setState({
      monitoring: makeMonitoring({ health: health({ topologyDrift: { available: false } }) }),
    });
    render(<MonitoringSection />);
    // 缺表格显示无此表（文字而非颜色）——健康格 + 卡片各一处，故 getAllByText
    expect(screen.getAllByText('无此表').length).toBeGreaterThan(0);
  });

  it('点击健康格就地切换到详情外壳，返回全部恢复卡片网格', () => {
    useAppStore.setState({ monitoring: makeMonitoring() });
    render(<MonitoringSection />);
    // 默认全部 → 卡片网格可见（无详情外壳，描述文案不存在）
    expect(screen.queryByText(/逐帧演进/)).toBeNull();
    // 点击「拓扑漂移」健康格（name 为按钮完整可访问名，用正则子串匹配；[0]=健康格，先于 tab/卡片在 DOM 中）
    const driftCell = screen.getAllByRole('button', { name: /拓扑漂移/ })[0];
    act(() => { driftCell.click(); });
    // 详情外壳出现唯一描述文案（card grid 已替换为外壳）
    expect(screen.getByText(/逐帧演进/)).toBeTruthy();
    // 返回全部恢复网格（返回按钮 aria-label="返回全部"，与「← 返回全部」正文不同）
    act(() => { screen.getByRole('button', { name: '返回全部' }).click(); });
    expect(screen.queryByText(/逐帧演进/)).toBeNull();
  });
});
