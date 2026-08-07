// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { DatasetOverview } from '@/lib/types';
import OverviewDatasetPanel from '@/components/overview/dataset-overview-panel';

const overview: DatasetOverview = {
  run: { run_id: 'run-1', started_at_ns: 1_000_000, ended_at_ns: 9_000_000, scenario_id: 'scenario-x', mode: 'record', collector_version: '0.1.0' },
  snapshots: { count: 55 },
  graph: { nodes: 1461, edges: 3300 },
  alerts: { count: 13 },
  evidence: { count: 1823, by_match_level: { L5Substring: 830 } },
  claims: { count: 3, by_type: { TaintedContextUse: 3 }, by_severity: { Critical: 3 } },
  tracebacks: { count: 22 },
  intents: { count: 23, by_tool_name: { Bash: 9 }, by_authority_source: { SseResponseToolUse: 20 } },
  actions: { count: 474, by_action_type: { FileRead: 424 }, by_signal_class: { HighSignal: 100 } },
  pollution: { count: 219 },
  policy_evaluations: { count: 755 },
  processes: { count: 160 },
  tls_sessions: { count: 1067 },
  identity_process_map: { count: 196 },
  provisional_identities: { count: 196 },
  agent_identities: { count: 0 },
  message_bindings: { count: 0 },
  delegation_records: { count: 0 },
  subagent_boundaries: { count: 0 },
  refinements: { claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 },
  non_link_diagnostics: { count: 2223 },
};

describe('OverviewDatasetPanel — agentsight 风格数据源详情', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders the summary band with health badge and identity row', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    // health badge renders the StatusBadge with exact text "需关注";
    // use a function matcher so it does not match the longer healthReason text
    expect(screen.getByText((content) => content === '需关注')).toBeDefined();
    expect(screen.getByText(/scenario-x/)).toBeDefined();
    expect(screen.getByText(/record/)).toBeDefined();
  });

  it('renders 6 KPI tiles with real counts', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    // Some KPI labels also appear in stat cards ("进程", "告警"); verify they exist
    for (const label of ['进程', '文件活动', '工具调用', '网络', '责任声明', '告警']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText('160').length).toBeGreaterThan(0); // processes
    expect(screen.getAllByText('1067').length).toBeGreaterThan(0); // tls_sessions
  });

  it('renders the signal panel with aggregated ×count rows', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    expect(screen.getByText('上下文污染事件')).toBeDefined();
    expect(screen.getByText('× 219')).toBeDefined();
    expect(screen.getByText('弱匹配证据')).toBeDefined();
    expect(screen.getByText('× 830')).toBeDefined();
  });

  it('renders stat card groups', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    expect(screen.getByText('快照帧')).toBeDefined();
    // "意图动作" appears only as a stat card group category heading
    expect(screen.getByText('意图动作')).toBeDefined();
    // "策略评估" appears in both signals and stat cards — check it exists
    expect(screen.getAllByText('策略评估').length).toBeGreaterThan(0);
  });

  it('returns null when there is no overview (non-db sources)', () => {
    const { container } = render(<OverviewDatasetPanel overview={null} />);
    expect(container.firstChild).toBeNull();
  });
});
