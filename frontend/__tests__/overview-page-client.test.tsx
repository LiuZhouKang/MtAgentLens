// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import OverviewPageClient from '@/components/overview/overview-page-client';
import { useAppStore } from '@/lib/store';
import { demoAlerts, demoGraph } from '@/lib/demo-data';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe('OverviewPageClient', () => {
  beforeEach(() => {
    useAppStore.setState({
      activeRunId: null,
      alerts: demoAlerts,
      graph: demoGraph,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the overview dashboard with KPI cards and the responsibility network', () => {
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    // KPI strip
    expect(screen.getByText('Active Agents')).toBeDefined();
    expect(screen.getByText('Critical Alerts')).toBeDefined();
    // Responsibility network stage
    expect(screen.getByText('实时责任网络')).toBeDefined();
    expect(screen.getByText('Collector Ready')).toBeDefined();
    // Rail panels
    expect(screen.getByText('集群压力')).toBeDefined();
    expect(screen.getByText('责任告警')).toBeDefined();
    expect(screen.getByText('事件流')).toBeDefined();
  });

  it('shows the lead cluster focus panel', () => {
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    expect(screen.getByText('Lead Cluster')).toBeDefined();
  });

  it('shows the full-page loader when there is no data yet', () => {
    useAppStore.setState({ alerts: [], graph: { nodes: [], edges: [] } });
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    expect(screen.getAllByText('MtAgentLens').length).toBeGreaterThan(0);
  });

  it('dbAssets 为空时进程谱系面板显示空态而非报错', () => {
    // 前提：store 未导入 db（dbAssets=null）→ 进程谱系/委派树显示空态而非伪造
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    expect(screen.getByText(/进程谱系/i)).toBeTruthy();
    expect(screen.getByText(/导入含进程表的 \.db 后此处有数据/i)).toBeTruthy();
    expect(screen.getByText(/委派家族/i)).toBeTruthy();
    expect(screen.getByText(/此数据源无委派边界/i)).toBeTruthy();
  });

  it('renders the dataset overview panel when a db overview is present', () => {
    useAppStore.setState({
      alerts: demoAlerts,
      graph: demoGraph,
      overview: {
        run: null,
        snapshots: { count: 1 },
        graph: { nodes: demoGraph.nodes.length, edges: demoGraph.edges.length },
        alerts: { count: demoAlerts.length },
        evidence: { count: 0, by_match_level: {} },
        claims: { count: 0, by_type: {}, by_severity: {} },
        tracebacks: { count: 0 },
        intents: { count: 0, by_tool_name: {}, by_authority_source: {} },
        actions: { count: 0, by_action_type: {}, by_signal_class: {} },
        pollution: { count: 0 },
        policy_evaluations: { count: 0, violations: 0 },
        processes: { count: 0 },
        tls_sessions: { count: 0 },
        identity_process_map: { count: 0 },
        provisional_identities: { count: 0 },
        agent_identities: { count: 0 },
        message_bindings: { count: 0 },
        delegation_records: { count: 0 },
        subagent_boundaries: { count: 0 },
        refinements: { claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 },
        non_link_diagnostics: { count: 0 },
      },
    });
    render(<OverviewPageClient />, { wrapper: createWrapper() });
    expect(screen.getByText('数据源详情')).toBeDefined();
  });
});
